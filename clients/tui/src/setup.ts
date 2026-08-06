import {writeFile} from 'node:fs/promises';
import {CliRenderEvents, createCliRenderer} from '@opentui/core';
import {createSetupForm} from './setup-form.js';
import type {SetupDefaults} from './setup-model.js';

const rawDefaults = process.env['VIBESYS_SETUP_DEFAULTS'];
const resultPath = process.env['VIBESYS_SETUP_RESULT'];
if (!rawDefaults || !resultPath) {
  throw new Error('VIBESYS_SETUP_DEFAULTS and VIBESYS_SETUP_RESULT are required');
}

const defaults = JSON.parse(rawDefaults) as SetupDefaults;
const renderer = await createCliRenderer({exitOnCtrlC: false});
const form = createSetupForm(renderer, defaults);
renderer.start();

await new Promise<void>(resolve => renderer.once(CliRenderEvents.DESTROY, resolve));
if (form.selection !== undefined) {
  await writeFile(resultPath, JSON.stringify(form.selection), 'utf8');
}
form.destroy();
