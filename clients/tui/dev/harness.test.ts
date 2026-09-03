/**
 * Process-level tests for the replay harness.
 *
 * The harness is two processes and a shell script that wires them together, so
 * the things that go wrong with it are process lifetime and wire bytes, neither
 * of which a unit test over a helper would see. Both tests here run the real
 * scripts and speak the real protocol over a real socket.
 *
 * `tests/server/test_tui_dev_harness.py` covers the other half, the static
 * response bodies and the recorded fixtures, against the Python models. It
 * cannot reach anything the mock computes at runtime, because CI's pytest job
 * has no JavaScript runtime.
 */

import {afterEach, expect, test} from 'bun:test';
import {type ChildProcess, spawn, spawnSync} from 'node:child_process';
import {chmodSync, existsSync, mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {createConnection, type Socket} from 'node:net';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import type {RunEvent, RunSnapshot} from '@vibesys/backend-client';
import {
  type ActiveExecutionCheckpoint,
  initialCoreState,
  reduceEventBatch,
  reduceSnapshot,
} from '@vibesys/core-state';

const HERE = dirname(fileURLToPath(import.meta.url));
const MOCK_UI = join(HERE, 'mock-ui.sh');
const MOCK_SERVER = join(HERE, 'mock-server.ts');

/** Generous: these wait on process exits, not on anything that should be slow. */
const TEST_TIMEOUT_MS = 30_000;
const WAIT_TIMEOUT_MS = 15_000;

/**
 * Bootstrap size that stops inside the first agent execution.
 *
 * `queue-rs-payloads` opens one at sequence 37 and closes it at 126, so a
 * 40-event bootstrap is delivered mid-invocation: the client folds an active
 * execution out of it, and a snapshot taken at the same sequence has to agree.
 */
const MID_INVOCATION_BOOTSTRAP = 40;

const cleanups: (() => void)[] = [];

afterEach(() => {
  while (cleanups.length > 0) cleanups.pop()?.();
});

function scratchDirectory(prefix: string): string {
  const directory = mkdtempSync(join(tmpdir(), prefix));
  cleanups.push(() => rmSync(directory, {recursive: true, force: true}));
  return directory;
}

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitFor(what: string, condition: () => Promise<boolean>): Promise<void> {
  const deadline = Date.now() + WAIT_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await condition()) return;
    await delay(25);
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** Whether anything is still accepting connections on `socketPath`. */
function isListening(socketPath: string): Promise<boolean> {
  return new Promise(resolve => {
    const probe = createConnection(socketPath);
    probe.on('connect', () => {
      probe.destroy();
      resolve(true);
    });
    probe.on('error', () => {
      probe.destroy();
      resolve(false);
    });
  });
}

function connect(socketPath: string): Promise<Socket> {
  return new Promise((resolve, reject) => {
    const socket = createConnection(socketPath);
    socket.on('connect', () => {
      cleanups.push(() => socket.destroy());
      resolve(socket);
    });
    socket.on('error', reject);
  });
}

/** Resolves with the first message the server writes that `match` accepts. */
function nextMessage(
  socket: Socket,
  match: (message: Record<string, unknown>) => boolean,
): Promise<Record<string, unknown>> {
  return new Promise(resolve => {
    let buffer = '';
    socket.setEncoding('utf8');
    socket.on('data', chunk => {
      buffer += chunk;
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line) continue;
        const message = JSON.parse(line) as Record<string, unknown>;
        if (match(message)) resolve(message);
      }
    });
  });
}

function request(socket: Socket, payload: Record<string, unknown>): void {
  socket.write(`${JSON.stringify({protocol_version: 1, ...payload})}\n`);
}

test(
  'terminates the mock server when the client exits before it subscribes',
  async () => {
    const directory = scratchDirectory('vs-mock-owner-');
    const socketPath = join(directory, 'mock.sock');
    const logPath = join(directory, 'mock.log');
    const clientPath = join(directory, 'client.sh');
    // Stands in for a TUI that never reaches its first `subscribe`: a missing
    // or incompatible runtime, or an exception during initialisation.
    writeFileSync(clientPath, '#!/usr/bin/env bash\nexit 3\n');
    chmodSync(clientPath, 0o755);
    // Best effort, so a regression leaves no server behind for the next test.
    cleanups.push(() => void spawnSync('pkill', ['-f', socketPath]));

    const exitCode = await new Promise<number | null>(resolve => {
      const script = spawn(MOCK_UI, ['--speed', '0'], {
        env: {
          ...process.env,
          VS_MOCK_SOCKET: socketPath,
          VS_MOCK_LOG: logPath,
          VS_MOCK_CLIENT: clientPath,
        },
        stdio: 'ignore',
      });
      script.on('close', code => resolve(code));
    });

    // The runner waits for the bind before it execs the client and reports 1 if
    // that never happened, so 3 is also the evidence that this was a failure
    // after the socket existed.
    expect(exitCode).toBe(3);

    await waitFor('the mock server to exit', async () => !(await isListening(socketPath)));
    expect(existsSync(socketPath)).toBe(false);
  },
  TEST_TIMEOUT_MS,
);

test(
  'keeps the active execution when an equal-sequence snapshot follows the bootstrap',
  async () => {
    const directory = scratchDirectory('vs-mock-snapshot-');
    const socketPath = join(directory, 'mock.sock');
    const server: ChildProcess = spawn(
      'bun',
      [
        MOCK_SERVER,
        '--socket',
        socketPath,
        '--fixture',
        'queue-rs-payloads.jsonl',
        '--bootstrap',
        String(MID_INVOCATION_BOOTSTRAP),
        '--paused',
        '--owner-pid',
        String(process.pid),
      ],
      {stdio: 'ignore'},
    );
    cleanups.push(() => server.kill('SIGKILL'));
    await waitFor('the mock server to bind', () => isListening(socketPath));

    const stream = await connect(socketPath);
    const batchMessage = nextMessage(stream, message => message['type'] === 'event_batch');
    request(stream, {request_id: 'sub-1', type: 'subscribe'});
    const batch = await batchMessage;

    // A second connection, the way the TUI queries while its stream runs. Boot
    // issues both concurrently, so this answer can land after the batch.
    const control = await connect(socketPath);
    const snapshotMessage = nextMessage(control, message => message['request_id'] === 'snap-1');
    request(control, {request_id: 'snap-1', type: 'query.snapshot'});
    const snapshot = (await snapshotMessage)['snapshot'] as RunSnapshot;

    const throughSequence = batch['through_sequence'] as number;
    expect(throughSequence).toBe(MID_INVOCATION_BOOTSTRAP);
    // The equal-sequence case is the one `reduceSnapshot` accepts.
    expect(snapshot.sequence).toBe(throughSequence);

    const folded = reduceEventBatch(
      initialCoreState(),
      batch['events'] as RunEvent[],
      batch['active_executions'] as ActiveExecutionCheckpoint | undefined,
      throughSequence,
      batch['history_after_sequence'] as number,
    );
    // Precondition: the bootstrap really does stop inside an invocation.
    const running = Object.keys(folded.activeExecutions);
    expect(running).toHaveLength(1);

    expect(Object.keys(reduceSnapshot(folded, snapshot).activeExecutions)).toEqual(running);
  },
  TEST_TIMEOUT_MS,
);
