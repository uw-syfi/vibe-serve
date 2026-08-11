import {BoxRenderable, type CliRenderer, InputRenderable, TextRenderable} from '@opentui/core';
import type {SetupDefaults, SetupFields, SetupSelection} from './setup-model.js';
import {validateSetupSelection} from './setup-model.js';
import type {Theme} from './ui/theme.js';

export interface SetupForm {
  readonly selection: SetupSelection | undefined;
  destroy(): void;
}

/**
 * Build the pre-launch experiment form and wire its key handling.
 *
 * The form is a plain OpenTUI subtree mounted on `renderer.root`, kept separate
 * from the `setup.ts` entrypoint so it can be driven by a test renderer.
 */
export function createSetupForm(
  renderer: CliRenderer,
  defaults: SetupDefaults,
  fields: SetupFields,
  theme: Theme,
): SetupForm {
  const root = new BoxRenderable(renderer, {
    id: 'setup',
    width: '100%',
    height: '100%',
    flexDirection: 'column',
    paddingLeft: 2,
    paddingRight: 2,
    paddingTop: 1,
    backgroundColor: theme.canvas,
  });
  const title = new TextRenderable(renderer, {
    id: 'setup-title',
    height: 1,
    fg: theme.accent,
    content: fields.experiment ? 'VibeSys · New experiment' : 'VibeSys · Runs directory',
  });
  const instructions = new TextRenderable(renderer, {
    id: 'setup-instructions',
    height: 1,
    fg: theme.textMuted,
    content: fields.experiment
      ? 'Tab / Shift-Tab: move · Enter: launch · Esc: cancel · Clear owner for local-only'
      : 'Enter: launch · Esc: cancel',
  });
  const error = new TextRenderable(renderer, {
    id: 'setup-error',
    height: 1,
    fg: theme.error,
    content: '',
  });

  type FieldName =
    | 'runsDirectory'
    | 'inputPath'
    | 'experimentName'
    | 'repositoryOwner'
    | 'repositoryName'
    | 'visibility';
  const entries: Array<{box: BoxRenderable; input: InputRenderable}> = [];
  const inputs: Partial<Record<FieldName, InputRenderable>> = {};
  const addField = (name: FieldName, label: string, value: string, placeholder: string): void => {
    const entry = createField(renderer, label, value, placeholder, theme);
    entries.push(entry);
    inputs[name] = entry.input;
  };
  if (fields.runsDirectory) {
    addField('runsDirectory', 'Runs directory', defaults.runs_dir, 'runs directory');
  }
  if (fields.experiment) {
    addField('inputPath', 'Input bundle', defaults.input_path, 'examples/<input>');
    addField('experimentName', 'Experiment name', defaults.experiment_name, 'experiment name');
    addField(
      'repositoryOwner',
      'Repository owner',
      defaults.repository_owner ?? '',
      'local-only if empty',
    );
    addField('repositoryName', 'Repository name', defaults.repository_name, 'repository name');
    addField('visibility', 'Visibility', defaults.visibility, 'private | public | internal');
  }
  root.add(title);
  root.add(instructions);
  for (const entry of entries) root.add(entry.box);
  root.add(error);
  renderer.root.add(root);

  let selected: SetupSelection | undefined;
  let focused = 0;
  entries[focused]?.input.focus();

  const readSelection = (): SetupSelection => {
    const runsDirectory = inputs.runsDirectory?.value ?? defaults.runs_dir;
    if (!fields.experiment) return {kind: 'runs-directory', runsDirectory};
    return {
      kind: 'experiment',
      runsDirectory,
      inputPath: inputs.inputPath?.value ?? '',
      experimentName: inputs.experimentName?.value ?? '',
      repositoryOwner: inputs.repositoryOwner?.value ?? '',
      repositoryName: inputs.repositoryName?.value ?? '',
      visibility: inputs.visibility?.value ?? '',
    };
  };
  const moveFocus = (offset: number): void => {
    entries[focused]?.input.blur();
    focused = (focused + offset + entries.length) % entries.length;
    entries[focused]?.input.focus();
  };
  // A validation message describes the input at the moment it was submitted.
  // Once the user navigates or edits, that snapshot is stale, so dismiss it
  // rather than leave the form asserting a problem the user may have just fixed.
  let activeError = '';
  const showError = (message: string): void => {
    activeError = message;
    error.content = message;
  };
  const dismissStaleError = (): void => {
    if (activeError.length > 0) showError('');
  };
  const onKey = (key: {name: string; shift: boolean; ctrl: boolean; preventDefault(): void}) => {
    if (key.name === 'tab') {
      dismissStaleError();
      moveFocus(key.shift ? -1 : 1);
      key.preventDefault();
      return;
    }
    if (key.name === 'escape' || (key.ctrl && key.name === 'c')) {
      key.preventDefault();
      renderer.destroy();
      return;
    }
    if (key.name !== 'return' && key.name !== 'enter') {
      dismissStaleError();
      return;
    }
    key.preventDefault();
    const candidate = readSelection();
    const validationError = validateSetupSelection(candidate);
    if (validationError !== undefined) {
      showError(validationError);
      return;
    }
    selected = candidate;
    renderer.destroy();
  };
  renderer.keyInput.on('keypress', onKey);

  return {
    get selection(): SetupSelection | undefined {
      return selected;
    },
    destroy(): void {
      renderer.keyInput.off('keypress', onKey);
      root.destroyRecursively();
    },
  };
}

function createField(
  renderer: CliRenderer,
  label: string,
  value: string,
  placeholder: string,
  theme: Theme,
): {box: BoxRenderable; input: InputRenderable} {
  const box = new BoxRenderable(renderer, {
    height: 3,
    width: '100%',
    border: true,
    borderStyle: 'rounded',
    borderColor: theme.border,
    title: ` ${label} `,
    paddingLeft: 1,
    paddingRight: 1,
  });
  const input = new InputRenderable(renderer, {
    width: '100%',
    value,
    placeholder,
    textColor: theme.textStrong,
    focusedTextColor: theme.textStrong,
  });
  box.add(input);
  return {box, input};
}
