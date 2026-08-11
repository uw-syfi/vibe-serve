import {describe, expect, it} from 'bun:test';
import {
  applySetupSelection,
  parseSetupDefaults,
  setupFieldsFor,
  validateSetupSelection,
} from './setup-model.js';

describe('interactive setup model', () => {
  const selection = {
    kind: 'experiment' as const,
    runsDirectory: '/repo/exp_env',
    inputPath: '/repo/examples/queue-spsc',
    experimentName: 'queue-spsc-20260720-120000',
    repositoryOwner: 'vibesys-playground',
    repositoryName: 'queue-spsc-20260720-120000',
    visibility: 'private',
  };

  it('parses backend defaults', () => {
    expect(
      parseSetupDefaults(
        JSON.stringify({
          runs_dir: selection.runsDirectory,
          input_path: selection.inputPath,
          experiment_name: selection.experimentName,
          repository_owner: selection.repositoryOwner,
          repository_name: selection.repositoryName,
          visibility: selection.visibility,
          theme: 'solarized-dark',
        }),
      ),
    ).toMatchObject({
      runs_dir: '/repo/exp_env',
      repository_owner: 'vibesys-playground',
      visibility: 'private',
      theme: 'solarized-dark',
    });
  });

  it('rejects backend defaults without a runs directory', () => {
    expect(() =>
      parseSetupDefaults(
        JSON.stringify({
          input_path: selection.inputPath,
          experiment_name: selection.experimentName,
          repository_owner: selection.repositoryOwner,
          repository_name: selection.repositoryName,
          visibility: selection.visibility,
          theme: 'dark',
        }),
      ),
    ).toThrow(/invalid interactive setup defaults/);
  });

  it('rejects backend defaults carrying an unknown theme', () => {
    expect(() =>
      parseSetupDefaults(
        JSON.stringify({
          runs_dir: selection.runsDirectory,
          input_path: selection.inputPath,
          experiment_name: selection.experimentName,
          repository_owner: selection.repositoryOwner,
          repository_name: selection.repositoryName,
          visibility: selection.visibility,
          theme: 'monokai',
        }),
      ),
    ).toThrow(/invalid interactive setup defaults/);
  });

  it('replaces launch values with the confirmed form values', () => {
    expect(
      applySetupSelection(
        ['--input=old', '--exp-name', 'old-name', '--runs-dir=old-runs', '--backend', 'cpu'],
        selection,
      ),
    ).toEqual([
      '--backend',
      'cpu',
      '--runs-dir',
      selection.runsDirectory,
      '--input',
      selection.inputPath,
      '--exp-name',
      selection.experimentName,
      '--repo',
      `${selection.repositoryOwner}/${selection.repositoryName}`,
      '--repo-visibility',
      'private',
    ]);
  });

  it('allows clearing the owner for a local-only experiment', () => {
    const local = {...selection, repositoryOwner: ''};

    expect(validateSetupSelection(local)).toBeUndefined();
    expect(applySetupSelection([], local)).toContain('--local');
    expect(applySetupSelection([], local)).not.toContain('--repo');
  });

  it('validates repository and required fields', () => {
    expect(validateSetupSelection({...selection, runsDirectory: ''})).toContain('Runs directory');
    expect(validateSetupSelection({...selection, inputPath: ''})).toContain('Input bundle');
    expect(validateSetupSelection({...selection, repositoryOwner: 'bad/owner'})).toContain('owner');
    expect(validateSetupSelection({...selection, visibility: 'secret'})).toContain('Visibility');
  });

  it('preserves skip-mode arguments while applying a directory-only selection', () => {
    expect(
      applySetupSelection(['--resume', 'saved-run', '--backend', 'cpu', '--runs-dir=old-runs'], {
        kind: 'runs-directory',
        runsDirectory: '/repo/new-runs',
      }),
    ).toEqual(['--resume', 'saved-run', '--backend', 'cpu', '--runs-dir', '/repo/new-runs']);
  });

  it('selects explicit fields for each interactive launch mode', () => {
    expect(setupFieldsFor(['--input', 'example'])).toEqual({
      experiment: true,
      runsDirectory: true,
    });
    expect(setupFieldsFor(['--input', 'example', '--runs-dir=chosen'])).toEqual({
      experiment: true,
      runsDirectory: false,
    });

    for (const argv of [['--resume'], ['--local'], ['--repo=owner/name'], ['--stub-agent']]) {
      expect(setupFieldsFor(argv)).toEqual({experiment: false, runsDirectory: true});
      expect(setupFieldsFor([...argv, '--runs-dir', '/repo/runs'])).toBeUndefined();
    }

    expect(setupFieldsFor(['--headless'])).toBeUndefined();
    expect(setupFieldsFor(['--headless', '--runs-dir', '/repo/runs'])).toBeUndefined();
  });
});
