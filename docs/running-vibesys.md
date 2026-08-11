# Running VibeSys

VibeSys has two workspace models. Omit `--runs-dir` to optimize an existing
project in place. Pass `--runs-dir` to create a separate experiment workspace.

## Choose a Workflow

| Workflow | Choose it when | State location |
| --- | --- | --- |
| In-place project | The candidate source already exists and the default local agent loop is sufficient. | The project source, Git history, and `.vs/` directory. |
| Materialized experiment | VibeSys must construct an isolated workspace or use seeded inputs, Docker, Modal, active profilers, remote repositories, or another outer loop. | A new experiment below `--runs-dir`. |

The input can be a project or bundle directory selected with `--input`. In
materialized mode, automation may instead provide the same input contract with
`--input-*` flags. That changes how the input is supplied, not the workspace
model.

| Capability | In-place project | Materialized experiment |
| --- | --- | --- |
| Edit existing candidate source directly | Yes | No, source is copied or materialized first |
| `agent` outer loop | Yes | Yes |
| `plain` and `evolve` outer loops | No | Yes |
| Native local execution | Yes | Yes |
| Docker or Modal | No | Yes |
| Active profilers | No | Yes |
| DeepAgents | No | Yes |
| Workspace seeds and Git-pinned sources | No | Yes |
| GitHub-backed experiment repository | No | Yes |

## In-Place Projects

This is the default workflow. The project must contain the candidate source,
`OBJECTIVE.md`, and `vibesys.input.toml`. A root `agent.toml` may configure the
coding agent, model, and hardware backend.

The directory must be the root of its Git repository, not a subdirectory of
another repository. An existing repository needs a baseline commit and a clean
worktree. A directory outside Git is initialized automatically. Keep
`agent.toml` and root `.env*` files untracked; VibeSys rejects them if they are
recoverable from Git history.

Launch from the project root:

```bash
cd /path/to/project
vibesys validate
vibesys --max-rounds 4
```

Passing the project explicitly is equivalent. When `agent.toml` lives in that
project, select it too because configuration is resolved from the launch
directory:

```bash
vibesys \
  --input /path/to/project \
  --config /path/to/project/agent.toml \
  --max-rounds 4
```

Each fresh run receives a `vibesys/<run-id>` branch. Candidate source remains at
its normal paths, portable run records are committed below `.vs/runs/`, and
machine-local state and logs live below the ignored `.vs/local/` directory.

A plain launch always starts a new run. Resume the current or newest run, or
select one by ID:

```bash
vibesys --resume
vibesys --resume <run-id>
```

In-place execution supports the `agent` outer loop, a CLI coding agent, the
native host sandbox, and profiler `none`. Use a materialized experiment for the
capabilities marked unavailable in the table above.

## Materialized Experiments

Passing `--runs-dir` tells VibeSys to create a separate experiment workspace.
The input directory can already contain candidate source, or its manifest can
declare a workspace seed or Git-pinned sources for VibeSys to materialize.
Checked-in examples that use separate starters and evaluators use this workflow.

Keep an experiment local with `--local`:

```bash
vibesys \
  --runs-dir /work/vibesys-runs \
  --local \
  --input /path/to/input-bundle
```

Without `--local`, a fresh materialized run creates a GitHub-backed experiment
repository. Authenticate the GitHub CLI first, then optionally choose its name:

```bash
gh auth login
vibesys \
  --runs-dir /work/vibesys-runs \
  --repo my-org/my-experiment \
  --input /path/to/input-bundle
```

Materialized experiments support the `agent`, `plain`, and `evolve` outer
loops, plus Docker, Modal, active profilers, custom skills, and DeepAgents. See
the [CLI reference](cli-flags.md) for the supported flag combinations.

### Resume a Materialized Experiment

Always provide `--runs-dir` when resuming this workflow. The resume target may
be a collection run ID, a local experiment directory, a GitHub `OWNER/NAME`, or
a cloneable URL:

```bash
vibesys --runs-dir /work/vibesys-runs --resume <run-id>
vibesys --runs-dir /work/vibesys-runs --resume /path/to/experiment
vibesys --runs-dir /work/vibesys-runs --resume my-org/my-experiment
vibesys --runs-dir /work/vibesys-runs --resume https://github.com/my-org/my-experiment.git
```

## Configure Inputs with CLI Flags

Automation can provide the input contract without creating an input bundle by
hand. This form requires `--runs-dir`. VibeSys writes an internal
`OBJECTIVE.md` and `vibesys.input.toml` below `<runs-dir>/_inputs/`, then uses
the normal materialized workflow.

The following example stages candidate source and evaluator programs from local
directories:

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

Use this form for CI or programmatic integrations. For a project maintained by
a person, checked-in `OBJECTIVE.md` and `vibesys.input.toml` files are easier to
review and reproduce.

## Presentation and Validation

The interactive and headless launchers run the same engine:

- `vibesys` launches the TUI when attached to a terminal.
- `vibesys --headless` disables the TUI.
- Non-interactive execution, such as CI, runs headless automatically.
- `vibesys validate [INPUT]` checks the static input contract without starting
  an agent or executing the checker and benchmark.

These presentation choices do not change the workspace model. Local, Docker,
and Modal select where commands execute; `agent`, `plain`, and `evolve` select
the search loop.

See the [CLI reference](cli-flags.md) for every flag and the
[`examples/`](../examples/) directory for complete objectives and manifests.
