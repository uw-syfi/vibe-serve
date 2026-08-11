import {CliRenderEvents} from '@opentui/core';
import type {SessionController} from './session-controller.js';
import type {OpenTuiApp} from './ui/app.js';

export interface RuntimeRenderer {
  start(): void;
  destroy(): void;
  once(event: CliRenderEvents, listener: () => void): unknown;
}

export async function runTuiSession(
  renderer: RuntimeRenderer,
  controller: Pick<SessionController, 'start' | 'stop'>,
  app: OpenTuiApp,
  completeStartupSmoke?: () => Promise<void>,
): Promise<void> {
  renderer.start();
  try {
    await controller.start();
    if (completeStartupSmoke) await completeStartupSmoke();
    else await new Promise<void>(resolve => renderer.once(CliRenderEvents.DESTROY, resolve));
  } finally {
    try {
      renderer.destroy();
    } finally {
      try {
        app.destroy();
      } finally {
        await controller.stop();
      }
    }
  }
}
