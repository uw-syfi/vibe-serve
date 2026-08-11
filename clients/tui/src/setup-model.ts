import {isThemeName, type ThemeName} from './ui/theme.js';

export const REPOSITORY_VISIBILITIES = ['private', 'public', 'internal'] as const;

export type RepositoryVisibility = (typeof REPOSITORY_VISIBILITIES)[number];

export interface SetupDefaults {
  runs_dir: string;
  input_path: string;
  experiment_name: string;
  repository_owner: string | null;
  repository_name: string;
  visibility: RepositoryVisibility;
  theme: ThemeName;
}

export interface SetupFields {
  experiment: boolean;
  runsDirectory: boolean;
}

export interface ExperimentSetupSelection {
  kind: 'experiment';
  runsDirectory: string;
  inputPath: string;
  experimentName: string;
  repositoryOwner: string;
  repositoryName: string;
  visibility: string;
}

export interface RunsDirectorySetupSelection {
  kind: 'runs-directory';
  runsDirectory: string;
}

export type SetupSelection = ExperimentSetupSelection | RunsDirectorySetupSelection;

const REPOSITORY_COMPONENT = /^[A-Za-z0-9_.-]+$/;

export function parseSetupDefaults(text: string): SetupDefaults {
  const value = JSON.parse(text) as Partial<SetupDefaults>;
  if (
    typeof value.runs_dir !== 'string' ||
    typeof value.input_path !== 'string' ||
    typeof value.experiment_name !== 'string' ||
    (value.repository_owner !== null && typeof value.repository_owner !== 'string') ||
    typeof value.repository_name !== 'string' ||
    !REPOSITORY_VISIBILITIES.includes(value.visibility as RepositoryVisibility) ||
    !isThemeName(value.theme)
  ) {
    throw new Error('backend returned invalid interactive setup defaults');
  }
  return value as SetupDefaults;
}

export function validateSetupSelection(selection: SetupSelection): string | undefined {
  if (selection.runsDirectory.trim().length === 0) return 'Runs directory is required.';
  if (selection.kind === 'runs-directory') return undefined;
  if (selection.inputPath.trim().length === 0) return 'Input bundle is required.';
  if (selection.experimentName.trim().length === 0) return 'Experiment name is required.';

  const owner = selection.repositoryOwner.trim();
  if (owner.length === 0) return undefined;
  if (!REPOSITORY_COMPONENT.test(owner)) {
    return 'Repository owner must be one GitHub user or organization name.';
  }
  if (!REPOSITORY_COMPONENT.test(selection.repositoryName.trim())) {
    return 'Repository name may contain letters, numbers, dot, underscore, and hyphen.';
  }
  if (!REPOSITORY_VISIBILITIES.includes(selection.visibility.trim() as RepositoryVisibility)) {
    return 'Visibility must be private, public, or internal.';
  }
  return undefined;
}

export function applySetupSelection(argv: string[], selection: SetupSelection): string[] {
  const withoutRunsDirectory = withoutOptions(argv, ['--runs-dir']);
  const result =
    selection.kind === 'experiment'
      ? withoutOptions(withoutRunsDirectory, [
          '--input',
          '--exp-name',
          '--repo',
          '--repo-visibility',
          '--local',
        ])
      : withoutRunsDirectory;
  result.push('--runs-dir', selection.runsDirectory.trim());
  if (selection.kind === 'runs-directory') return result;
  result.push('--input', selection.inputPath.trim());
  result.push('--exp-name', selection.experimentName.trim());

  const owner = selection.repositoryOwner.trim();
  if (owner.length > 0) {
    result.push('--repo', `${owner}/${selection.repositoryName.trim()}`);
    result.push('--repo-visibility', selection.visibility.trim());
  } else {
    result.push('--local');
  }
  return result;
}

export function setupFieldsFor(argv: string[]): SetupFields | undefined {
  if (hasOption(argv, '--headless')) return undefined;

  const runsDirectory = !hasOption(argv, '--runs-dir');
  const experiment = !['--resume', '--repo', '--local', '--stub-agent'].some(option =>
    hasOption(argv, option),
  );
  if (!experiment && !runsDirectory) return undefined;
  return {experiment, runsDirectory};
}

function hasOption(argv: string[], option: string): boolean {
  return argv.some(argument => argument === option || argument.startsWith(`${option}=`));
}

function withoutOptions(argv: string[], options: string[]): string[] {
  const result: string[] = [];
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === undefined) continue;
    const option = options.find(
      candidate => argument === candidate || argument.startsWith(`${candidate}=`),
    );
    if (option === undefined) {
      result.push(argument);
      continue;
    }
    if (argument === option) index += 1;
  }
  return result;
}
