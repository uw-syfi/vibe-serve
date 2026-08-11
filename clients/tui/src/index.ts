import {createCliRenderer} from '@opentui/core';
import {writeFile} from 'node:fs/promises';
import {SupervisionClient} from './client.js';
import {runTuiSession} from './runtime.js';
import {SocketSessionController} from './session-controller.js';
import {createOpenTuiApp} from './ui/app.js';
import {resolveTheme} from './ui/theme.js';

const socketPath = process.env['VIBESYS_CONTROL_SOCKET'];
if (!socketPath) throw new Error('VIBESYS_CONTROL_SOCKET is required');

const client = await SupervisionClient.connect(socketPath);
const renderer = await createCliRenderer({exitOnCtrlC: true});
const controller = new SocketSessionController(
  client,
  resolveTheme(process.env['VIBESYS_THEME']).name,
);
const app = createOpenTuiApp(renderer, controller);
const startupSmokeMarker = process.env['VIBESYS_RELEASE_SMOKE_MARKER'];
const completeStartupSmoke = startupSmokeMarker
  ? async () => {
      await writeFile(
        startupSmokeMarker,
        'renderer initialized; control protocol exchanged\n',
        {flag: 'wx'},
      );
    }
  : undefined;
await runTuiSession(renderer, controller, app, completeStartupSmoke);
