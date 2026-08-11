import {afterEach, describe, expect, it} from 'bun:test';
import {access, chmod, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {launch} from './launcher.js';

let tempDir: string | undefined;
const savedPython = process.env['VIBESYS_PYTHON'];
const savedRuntime = process.env['VIBESYS_TUI_RUNTIME'];
const savedEntrypoint = process.env['VIBESYS_TUI_ENTRYPOINT'];
const savedTermFile = process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'];
const savedArgsFile = process.env['VIBESYS_FAKE_BACKEND_ARGS_FILE'];
const savedReleaseSmokeMarker = process.env['VIBESYS_RELEASE_SMOKE_MARKER'];
const originalCwd = process.cwd();

afterEach(async () => {
  process.chdir(originalCwd);
  if (savedPython === undefined) delete process.env['VIBESYS_PYTHON'];
  else process.env['VIBESYS_PYTHON'] = savedPython;
  if (savedRuntime === undefined) delete process.env['VIBESYS_TUI_RUNTIME'];
  else process.env['VIBESYS_TUI_RUNTIME'] = savedRuntime;
  if (savedEntrypoint === undefined) delete process.env['VIBESYS_TUI_ENTRYPOINT'];
  else process.env['VIBESYS_TUI_ENTRYPOINT'] = savedEntrypoint;
  if (savedTermFile === undefined) delete process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'];
  else process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'] = savedTermFile;
  if (savedArgsFile === undefined) delete process.env['VIBESYS_FAKE_BACKEND_ARGS_FILE'];
  else process.env['VIBESYS_FAKE_BACKEND_ARGS_FILE'] = savedArgsFile;
  if (savedReleaseSmokeMarker === undefined) delete process.env['VIBESYS_RELEASE_SMOKE_MARKER'];
  else process.env['VIBESYS_RELEASE_SMOKE_MARKER'] = savedReleaseSmokeMarker;
  delete process.env['VIBESYS_FAKE_FRONTEND_THEME_FILE'];
  if (tempDir) await rm(tempDir, {recursive: true, force: true});
  tempDir = undefined;
});

describe('launcher', () => {
  it('publishes simple installed command names', async () => {
    const packageJsonPath = join(dirname(fileURLToPath(import.meta.url)), '..', 'package.json');
    const packageJson = JSON.parse(await readFile(packageJsonPath, 'utf8')) as {
      bin?: Record<string, string>;
    };

    expect(packageJson.bin).toEqual({
      vibesys: './dist/launcher.js',
      vs: './dist/launcher.js',
    });
  });

  it('starts a headless backend, waits for readiness, and runs the frontend', async () => {
    tempDir = await mkdtemp(join(tmpdir(), 'vs-launcher-test-'));
    const backendTerminated = join(tempDir, 'backend-terminated');
    const backend = await writeExecutable(
      'fake-backend.mjs',
      `
import {writeFileSync} from 'node:fs';
import {createServer} from 'node:net';

const socketPath = process.argv[process.argv.indexOf('--control-socket') + 1];
const server = createServer(socket => {
  let buffer = '';
  socket.setEncoding('utf8');
  socket.on('data', chunk => {
    buffer += chunk;
    if (!buffer.includes('\\n')) return;
    const request = JSON.parse(buffer.split('\\n')[0]);
    socket.end(JSON.stringify({
      protocol_version: 1,
      request_id: request.request_id,
      timestamp: new Date().toISOString(),
      ok: true,
      events: [],
    }) + '\\n');
  });
});
server.listen(socketPath);
process.on('SIGTERM', () => {
  writeFileSync(process.env.VIBESYS_FAKE_BACKEND_TERM_FILE, 'terminated');
  server.close(() => process.exit(0));
});
`,
    );
    const frontend = await writeExecutable(
      'fake-frontend.mjs',
      `
if (!process.env.VIBESYS_CONTROL_SOCKET) process.exit(7);
process.exit(0);
`,
    );

    process.env['VIBESYS_PYTHON'] = backend;
    process.env['VIBESYS_TUI_RUNTIME'] = frontend;
    process.env['VIBESYS_TUI_ENTRYPOINT'] = frontend;
    process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'] = backendTerminated;

    await expect(launch(['--stub-agent', '--runs-dir', '/tmp/vibesys-test-runs'])).resolves.toBe(0);
    await access(backendTerminated);
  });

  it('makes a successful frontend authoritative only in release smoke mode', async () => {
    tempDir = await mkdtemp(join(tmpdir(), 'vs-launcher-test-'));
    const backendTerminated = join(tempDir, 'backend-terminated');
    const smokeMarker = join(tempDir, 'frontend-started');
    const backend = await writeExecutable(
      'race-backend.mjs',
      `
import {writeFileSync} from 'node:fs';
import {createServer} from 'node:net';

const socketPath = process.argv[process.argv.indexOf('--control-socket') + 1];
const server = createServer(socket => {
  socket.once('data', data => {
    const request = JSON.parse(data.toString().split('\\n')[0]);
    socket.end(JSON.stringify({
      protocol_version: 1,
      request_id: request.request_id,
      timestamp: new Date().toISOString(),
      ok: true,
      events: [],
    }) + '\\n');
    setTimeout(() => process.exit(2), 1000);
  });
});
server.listen(socketPath);
process.on('SIGTERM', () => {
  writeFileSync(process.env.VIBESYS_FAKE_BACKEND_TERM_FILE, 'terminated');
  server.close(() => process.exit(0));
});
`,
    );
    const frontend = await writeExecutable(
      'smoke-frontend.mjs',
      `
import {writeFileSync} from 'node:fs';

if (!process.env.VIBESYS_CONTROL_SOCKET) process.exit(7);
if (process.env.VIBESYS_RELEASE_SMOKE_MARKER) {
  writeFileSync(process.env.VIBESYS_RELEASE_SMOKE_MARKER, 'started');
}
process.exit(0);
`,
    );

    process.env['VIBESYS_PYTHON'] = backend;
    process.env['VIBESYS_TUI_RUNTIME'] = process.execPath;
    process.env['VIBESYS_TUI_ENTRYPOINT'] = frontend;

    delete process.env['VIBESYS_RELEASE_SMOKE_MARKER'];
    process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'] = join(tempDir, 'normal-backend-terminated');
    await expect(launch(['--stub-agent', '--runs-dir', '/tmp/vibesys-test-runs'])).resolves.toBe(2);

    process.env['VIBESYS_RELEASE_SMOKE_MARKER'] = smokeMarker;
    process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'] = backendTerminated;
    await expect(launch(['--stub-agent', '--runs-dir', '/tmp/vibesys-test-runs'])).resolves.toBe(0);
    expect(await readFile(smokeMarker, 'utf8')).toBe('started');
    expect(await readFile(backendTerminated, 'utf8')).toBe('terminated');
  });

  it('runs validation directly without starting the interactive client', async () => {
    tempDir = await mkdtemp(join(tmpdir(), 'vs-launcher-test-'));
    const backend = await writeExecutable(
      'fake-backend.mjs',
      `
process.exit(
  process.argv.includes('validate') && process.argv.includes('examples/kv-store') ? 0 : 9,
);
`,
    );

    process.env['VIBESYS_PYTHON'] = backend;
    process.env['VIBESYS_TUI_RUNTIME'] = join(tempDir, 'missing-runtime');

    await expect(launch(['validate', 'examples/kv-store'])).resolves.toBe(0);
  });

  it('launches in place without setup and preserves explicit launch arguments', async () => {
    tempDir = await mkdtemp(join(tmpdir(), 'vs-launcher-test-'));
    const backendTerminated = join(tempDir, 'backend-terminated');
    const backendArgs = join(tempDir, 'backend-args.json');
    const defaultsInvoked = join(tempDir, 'defaults-invoked');
    const backendCwd = join(tempDir, 'backend-cwd');
    const backend = await writeExecutable(
      'in-place-backend.mjs',
      `
import {writeFileSync} from 'node:fs';
import {createServer} from 'node:net';

if (process.argv.includes('tui-defaults')) {
  writeFileSync(${JSON.stringify(defaultsInvoked)}, 'invoked');
  process.exit(9);
}
writeFileSync(${JSON.stringify(backendCwd)}, process.cwd());
writeFileSync(process.env.VIBESYS_FAKE_BACKEND_ARGS_FILE, JSON.stringify(process.argv.slice(2)));
const socketPath = process.argv[process.argv.indexOf('--control-socket') + 1];
const server = createServer(socket => {
  socket.once('data', data => {
    const request = JSON.parse(data.toString().split('\\n')[0]);
    socket.end(JSON.stringify({
      protocol_version: 1,
      request_id: request.request_id,
      timestamp: new Date().toISOString(),
      ok: true,
      events: [],
    }) + '\\n');
  });
});
server.listen(socketPath);
process.on('SIGTERM', () => {
  writeFileSync(process.env.VIBESYS_FAKE_BACKEND_TERM_FILE, 'terminated');
  server.close(() => process.exit(0));
});
`,
    );
    const frontend = await writeExecutable(
      'in-place-frontend.mjs',
      `
import {writeFileSync} from 'node:fs';
writeFileSync(process.env.VIBESYS_FAKE_FRONTEND_THEME_FILE, process.env.VIBESYS_THEME ?? '');
process.exit(0);
`,
    );

    process.env['VIBESYS_PYTHON'] = backend;
    process.env['VIBESYS_TUI_RUNTIME'] = process.execPath;
    process.env['VIBESYS_TUI_ENTRYPOINT'] = frontend;
    process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'] = backendTerminated;
    process.env['VIBESYS_FAKE_BACKEND_ARGS_FILE'] = backendArgs;
    const frontendTheme = join(tempDir, 'frontend-theme');
    process.env['VIBESYS_FAKE_FRONTEND_THEME_FILE'] = frontendTheme;
    process.chdir(tempDir);

    await expect(launch([])).resolves.toBe(0);
    const implicitArgs = JSON.parse(await readFile(backendArgs, 'utf8')) as string[];
    expect(implicitArgs).not.toContain('--runs-dir');
    expect(implicitArgs).not.toContain('--input');
    expect(implicitArgs).not.toContain('--repo');
    expect(implicitArgs).not.toContain('--exp-name');
    await expect(access(defaultsInvoked)).rejects.toThrow();
    expect(await readFile(backendCwd, 'utf8')).toBe(tempDir);
    expect(await readFile(frontendTheme, 'utf8')).toBe('dark');

    await expect(
      launch([
        '--input',
        'examples/queue-spsc',
        '--runs-dir',
        '/repo/legacy-runs',
        '--theme',
        'solarized-dark',
      ]),
    ).resolves.toBe(0);
    const explicitArgs = JSON.parse(await readFile(backendArgs, 'utf8')) as string[];
    expect(explicitArgs.filter(argument => argument === '--runs-dir')).toHaveLength(1);
    expect(explicitArgs[explicitArgs.indexOf('--runs-dir') + 1]).toBe('/repo/legacy-runs');
    expect(await readFile(frontendTheme, 'utf8')).toBe('solarized-dark');
    await access(backendTerminated);
  });

  it('rejects an unknown --theme before starting any process', async () => {
    tempDir = await mkdtemp(join(tmpdir(), 'vibesys-launcher-'));
    const backend = await writeExecutable('unused-backend.mjs', 'process.exit(0);');
    const frontend = await writeExecutable('unused-frontend.mjs', 'process.exit(0);');
    process.env['VIBESYS_PYTHON'] = backend;
    process.env['VIBESYS_TUI_RUNTIME'] = process.execPath;
    process.env['VIBESYS_TUI_ENTRYPOINT'] = frontend;

    await expect(launch(['--theme', 'monokai'])).resolves.toBe(2);
  });

  it('returns the backend argument error for an explicitly empty runs directory', async () => {
    tempDir = await mkdtemp(join(tmpdir(), 'vibesys-launcher-'));
    const frontendMarker = join(tempDir, 'frontend-started');
    const failureMarker = join(tempDir, 'configuration-failure.json');
    const frontend = await writeExecutable(
      'unused-frontend.mjs',
      `
import {writeFileSync} from 'node:fs';
import {createConnection} from 'node:net';
writeFileSync(${JSON.stringify(frontendMarker)}, 'started');
const socket = createConnection(process.env.VIBESYS_CONTROL_SOCKET);
let buffer = '';
socket.once('connect', () => socket.write(JSON.stringify({
  protocol_version: 1,
  request_id: 'launcher-empty-runs-dir',
  timestamp: '1970-01-01T00:00:00Z',
  type: 'subscribe',
  after_sequence: 0,
}) + '\\n'));
socket.setEncoding('utf8');
socket.on('data', chunk => {
  buffer += chunk;
  while (buffer.includes('\\n')) {
    const newline = buffer.indexOf('\\n');
    const message = JSON.parse(buffer.slice(0, newline));
    buffer = buffer.slice(newline + 1);
    const events = message.type === 'event' ? [message.event] :
      message.type === 'event_batch' ? message.events : [];
    const failure = events.find(event => event.type === 'configuration_failed');
    if (failure) {
      writeFileSync(${JSON.stringify(failureMarker)}, JSON.stringify(failure));
      socket.end();
      return;
    }
    if (events.some(event => event.type === 'run_finished' || event.type === 'run_failed')) {
      socket.end();
      return;
    }
  }
});
socket.once('close', () => process.exit(0));
`,
    );
    const python = join(dirname(fileURLToPath(import.meta.url)), '../../../.venv/bin/python');
    process.chdir(tempDir);
    process.env['VIBESYS_PYTHON'] = python;
    process.env['VIBESYS_TUI_RUNTIME'] = process.execPath;
    process.env['VIBESYS_TUI_ENTRYPOINT'] = frontend;

    await expect(
      launch(['--stub-agent', '--local', '--max-rounds', '0', '--runs-dir=']),
    ).resolves.toBe(2);
    await access(frontendMarker);
    const failure = JSON.parse(await readFile(failureMarker, 'utf8')) as {
      data: {code: string; stage: string; message: string};
    };
    expect(failure.data.code).toBe('invalid_arguments');
    expect(failure.data.stage).toBe('argument_parsing');
    expect(failure.data.message).toContain('argument --runs-dir: must not be empty');
    await expect(access(join(tempDir, 'exp_env'))).rejects.toThrow();
  }, 15_000);
});

async function writeExecutable(name: string, source: string): Promise<string> {
  if (!tempDir) throw new Error('tempDir is required');
  const path = join(tempDir, name);
  await writeFile(path, `#!/usr/bin/env node\n${source}`);
  await chmod(path, 0o755);
  return path;
}
