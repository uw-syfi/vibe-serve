import {createTestRenderer} from '@opentui/core/testing';
import {afterEach, describe, expect, it} from 'bun:test';
import {createSetupForm} from './setup-form.js';
import type {SetupDefaults} from './setup-model.js';
import {resolveTheme} from './ui/theme.js';

const THEME = resolveTheme('dark');

const cleanup: Array<() => void> = [];

afterEach(() => {
  for (const destroy of cleanup.splice(0).reverse()) destroy();
});

const DEFAULTS: SetupDefaults = {
  input_path: 'examples/x',
  experiment_name: 'x',
  repository_owner: '',
  repository_name: 'x',
  visibility: 'private',
  theme: 'dark',
};

const OWNER_ERROR = 'Repository owner must be one GitHub user or organization name.';

describe('interactive setup form', () => {
  it('dismisses a stale validation error once the user edits the input', async () => {
    const testRenderer = await createTestRenderer({width: 92, height: 24});
    const form = createSetupForm(testRenderer.renderer, DEFAULTS, THEME);
    cleanup.push(() => {
      form.destroy();
      testRenderer.renderer.destroy();
    });

    // Move to the Repository owner field and submit an invalid value.
    testRenderer.mockInput.pressKey('TAB');
    testRenderer.mockInput.pressKey('TAB');
    await testRenderer.mockInput.typeText('a/b');
    testRenderer.mockInput.pressEnter();
    const errored = await testRenderer.waitForFrame(frame => frame.includes(OWNER_ERROR));
    expect(errored).toContain(OWNER_ERROR);

    // Correcting the value must clear the message, not keep asserting the input
    // is invalid after it has become valid again.
    testRenderer.mockInput.pressKey('BACKSPACE');
    testRenderer.mockInput.pressKey('BACKSPACE');
    const corrected = await testRenderer.waitForFrame(frame => !frame.includes(OWNER_ERROR));
    expect(corrected).not.toContain(OWNER_ERROR);
    expect(form.selection).toBeUndefined();
  });

  it('still blocks submission while the value is invalid', async () => {
    const testRenderer = await createTestRenderer({width: 92, height: 24});
    const form = createSetupForm(testRenderer.renderer, DEFAULTS, THEME);
    cleanup.push(() => {
      form.destroy();
      testRenderer.renderer.destroy();
    });

    testRenderer.mockInput.pressKey('TAB');
    testRenderer.mockInput.pressKey('TAB');
    await testRenderer.mockInput.typeText('bad/owner');
    testRenderer.mockInput.pressEnter();
    const frame = await testRenderer.waitForFrame(value => value.includes(OWNER_ERROR));
    expect(frame).toContain(OWNER_ERROR);
    expect(form.selection).toBeUndefined();
  });
});
