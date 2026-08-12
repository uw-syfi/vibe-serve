# Running VibeSys

Every VibeSys run operates on one canonical project. The project root contains
the candidate source, `OBJECTIVE.md`, `vibesys.input.toml`, `.git/`, and `.vs/`.
The project root is the agent's working directory.

```text
project/
├── .git/
├── .vs/
│   ├── project.json
│   ├── runs/<run-id>/          # committed run metadata and loop state
│   └── local/runs/<run-id>/    # logs and machine-local state
├── OBJECTIVE.md
├── vibesys.input.toml
└── candidate source
```

VibeSys commits candidate evolution and portable `.vs/runs/` metadata on a
`vibesys/<run-id>` branch. `.vs/local/`, `agent.toml`, and root `.env*` files
stay uncommitted and are hidden from coding agents.

## Start from the Current Project

Launch from a self-contained project root when its candidate source is already
present:

```bash
cd /path/to/project
vibesys validate
vibesys --max-rounds 4
```

`--input` selects the same project explicitly:

```bash
vibesys --input /path/to/project --config /path/to/project/agent.toml
```

The directory must be its Git repository root, or outside Git so VibeSys can
initialize a repository. An existing repository needs a baseline commit and a
clean worktree. Keep `agent.toml` and root `.env*` files out of Git history.

The `agent`, `plain`, and `evolve` outer loops all use this project model. Local,
Docker, and Modal execution change where commands run, not the on-disk layout.

## Create a Copied Project

Pass `--runs-dir` when the input should remain unchanged or its manifest uses a
workspace seed or Git-pinned sources:

```bash
vibesys \
  --runs-dir /work/vibesys-runs \
  --local \
  --input /path/to/input
```

VibeSys provisions a self-contained project at
`/work/vibesys-runs/<generated-run-id>/`. Candidate source is at that directory's
root. The copied manifest no longer depends on the original seed or source
paths, and a declared evaluator is copied below `_evaluator/`.

The source input's `.git/`, `.vs/`, `agent.toml`, and `.env*` files are never
copied. Agent configuration continues to come from the launch directory or an
explicit `--config` path.

Without `--local`, a fresh copied project is published to GitHub. Authenticate
the GitHub CLI first, then optionally select the repository name:

```bash
gh auth login
vibesys \
  --runs-dir /work/vibesys-runs \
  --repo my-org/my-experiment \
  --input /path/to/input
```

Publication pushes the existing `vibesys/<run-id>` history and retained evolve
candidate refs. It does not create an additional synchronization commit.

## Resume

Inside a project, resume the machine-local current run, then the newest run if
no current pointer exists. A run ID selects a specific run:

```bash
cd /path/to/project
vibesys --resume
vibesys --resume <run-id>
```

With `--runs-dir`, the resume argument selects a project in the collection. It
may be a directory name, a local path, a GitHub `OWNER/NAME`, or a cloneable
URL. VibeSys then resumes that project's current or newest run.

```bash
vibesys --runs-dir /work/vibesys-runs --resume <project-directory-name>
vibesys --runs-dir /work/vibesys-runs --resume /path/to/project
vibesys --runs-dir /work/vibesys-runs --resume my-org/my-experiment
vibesys --runs-dir /work/vibesys-runs --resume https://github.com/my-org/my-experiment.git
```

Omitted configuration flags are restored from `.vs/runs/<run-id>/run.json`.
The total round or generation limit may increase. Other recorded settings
cannot change during a resume.

## Supply an Input with CLI Flags

Automation can supply the input contract without first writing an input
directory. This form requires `--runs-dir`. VibeSys synthesizes the objective
and manifest, then provisions the same canonical project layout.

```bash
vibesys \
  --runs-dir /work/vibesys-runs \
  --local \
  --input-objective-file ./OBJECTIVE.md \
  --input-domain generic \
  --input-accuracy-command "python check.py" \
  --input-benchmark-command "python benchmark.py" \
  --input-benchmark-metric throughput \
  --input-benchmark-result-arg=--output-json \
  --input-evaluator-dir ./evaluation \
  --input-workspace-seed ./candidate
```

Use this form for CI or programmatic integrations. Checked-in
`OBJECTIVE.md` and `vibesys.input.toml` files are easier to review for projects
maintained by people.

## Presentation and Validation

- `vibesys` launches the TUI when attached to a terminal.
- `vibesys --headless` disables the TUI.
- Non-interactive execution runs headless automatically.
- `vibesys validate [INPUT]` checks the static input contract without starting
  an agent or executing the checker and benchmark.

See the [CLI reference](cli-flags.md) for every flag and
[`examples/`](../examples/) for complete objectives and manifests.
