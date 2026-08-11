import {writeFile} from 'node:fs/promises';
import {CliRenderEvents, createCliRenderer} from '@opentui/core';
import {createSetupForm} from './setup-form.js';
import type {SetupDefaults, SetupFields} from './setup-model.js';
import {resolveTheme} from './ui/theme.js';

const rawDefaults = process.env['VIBESYS_SETUP_DEFAULTS'];
const rawFields = process.env['VIBESYS_SETUP_FIELDS'];
const resultPath = process.env['VIBESYS_SETUP_RESULT'];
if (!rawDefaults || !rawFields || !resultPath) {
  throw new Error(
    'VIBESYS_SETUP_DEFAULTS, VIBESYS_SETUP_FIELDS, and VIBESYS_SETUP_RESULT are required',
  );
}

const defaults = JSON.parse(rawDefaults) as SetupDefaults;
const fields = JSON.parse(rawFields) as SetupFields;
const theme = resolveTheme(process.env['VIBESYS_THEME']);
const renderer = await createCliRenderer({exitOnCtrlC: false});
const form = createSetupForm(renderer, defaults, fields, theme);
renderer.start();

await new Promise<void>(resolve => renderer.once(CliRenderEvents.DESTROY, resolve));
if (form.selection !== undefined) {
  await writeFile(resultPath, JSON.stringify(form.selection), 'utf8');
}
form.destroy();
