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
const savedSetupEntrypoint = process.env['VIBESYS_SETUP_ENTRYPOINT'];
const savedTermFile = process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'];
const savedArgsFile = process.env['VIBESYS_FAKE_BACKEND_ARGS_FILE'];
const savedReleaseSmokeMarker = process.env['VIBESYS_RELEASE_SMOKE_MARKER'];

afterEach(async () => {
  if (savedPython === undefined) delete process.env['VIBESYS_PYTHON'];
  else process.env['VIBESYS_PYTHON'] = savedPython;
  if (savedRuntime === undefined) delete process.env['VIBESYS_TUI_RUNTIME'];
  else process.env['VIBESYS_TUI_RUNTIME'] = savedRuntime;
  if (savedEntrypoint === undefined) delete process.env['VIBESYS_TUI_ENTRYPOINT'];
  else process.env['VIBESYS_TUI_ENTRYPOINT'] = savedEntrypoint;
  if (savedSetupEntrypoint === undefined) delete process.env['VIBESYS_SETUP_ENTRYPOINT'];
  else process.env['VIBESYS_SETUP_ENTRYPOINT'] = savedSetupEntrypoint;
  if (savedTermFile === undefined) delete process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'];
  else process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'] = savedTermFile;
  if (savedArgsFile === undefined) delete process.env['VIBESYS_FAKE_BACKEND_ARGS_FILE'];
  else process.env['VIBESYS_FAKE_BACKEND_ARGS_FILE'] = savedArgsFile;
  if (savedReleaseSmokeMarker === undefined) delete process.env['VIBESYS_RELEASE_SMOKE_MARKER'];
  else process.env['VIBESYS_RELEASE_SMOKE_MARKER'] = savedReleaseSmokeMarker;
  delete process.env['VIBESYS_FAKE_SETUP_THEME_FILE'];
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

  it('runs configured repository setup before starting the backend', async () => {
    tempDir = await mkdtemp(join(tmpdir(), 'vs-launcher-test-'));
    const backendTerminated = join(tempDir, 'backend-terminated');
    const backendArgs = join(tempDir, 'backend-args.json');
    const backend = await writeExecutable(
      'setup-backend.mjs',
      `
import {writeFileSync} from 'node:fs';
import {createServer} from 'node:net';

if (process.argv.includes('tui-defaults')) {
  console.log(JSON.stringify({
    runs_dir: '/repo/exp_env',
    input_path: '/repo/examples/queue-spsc',
    experiment_name: 'queue-spsc-generated',
    repository_owner: 'vibesys-playground',
    repository_name: 'queue-spsc-generated',
    visibility: 'private',
    theme: 'solarized-dark',
  }));
  process.exit(0);
}
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
    const setup = await writeExecutable(
      'fake-setup.mjs',
      `
import {writeFileSync} from 'node:fs';
const defaults = JSON.parse(process.env.VIBESYS_SETUP_DEFAULTS);
writeFileSync(process.env.VIBESYS_FAKE_SETUP_THEME_FILE, process.env.VIBESYS_THEME ?? '');
writeFileSync(process.env.VIBESYS_SETUP_RESULT, JSON.stringify({
  kind: 'experiment',
  runsDirectory: defaults.runs_dir,
  inputPath: defaults.input_path,
  experimentName: defaults.experiment_name,
  repositoryOwner: defaults.repository_owner,
  repositoryName: defaults.repository_name,
  visibility: defaults.visibility,
}));
`,
    );
    const frontend = await writeExecutable(
      'setup-frontend.mjs',
      `
import {writeFileSync} from 'node:fs';
writeFileSync(process.env.VIBESYS_FAKE_FRONTEND_THEME_FILE, process.env.VIBESYS_THEME ?? '');
process.exit(0);
`,
    );

    process.env['VIBESYS_PYTHON'] = backend;
    process.env['VIBESYS_TUI_RUNTIME'] = process.execPath;
    process.env['VIBESYS_TUI_ENTRYPOINT'] = frontend;
    process.env['VIBESYS_SETUP_ENTRYPOINT'] = setup;
    process.env['VIBESYS_FAKE_BACKEND_TERM_FILE'] = backendTerminated;
    process.env['VIBESYS_FAKE_BACKEND_ARGS_FILE'] = backendArgs;
    const setupTheme = join(tempDir, 'setup-theme');
    const frontendTheme = join(tempDir, 'frontend-theme');
    process.env['VIBESYS_FAKE_SETUP_THEME_FILE'] = setupTheme;
    process.env['VIBESYS_FAKE_FRONTEND_THEME_FILE'] = frontendTheme;

    await expect(launch(['--input', 'examples/queue-spsc'])).resolves.toBe(0);
    const args = JSON.parse(await readFile(backendArgs, 'utf8')) as string[];
    expect(args.filter(argument => argument === '--runs-dir')).toHaveLength(1);
    expect(args[args.indexOf('--runs-dir') + 1]).toBe('/repo/exp_env');
    expect(args).toContain('vibesys-playground/queue-spsc-generated');
    expect(args).toContain('queue-spsc-generated');
    expect(await readFile(setupTheme, 'utf8')).toBe('solarized-dark');
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
});

async function writeExecutable(name: string, source: string): Promise<string> {
  if (!tempDir) throw new Error('tempDir is required');
  const path = join(tempDir, name);
  await writeFile(path, `#!/usr/bin/env node\n${source}`);
  await chmod(path, 0o755);
  return path;
}
