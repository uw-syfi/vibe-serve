# CLI Flags and Supported Combinations

This document is the canonical map for VibeSys's CLI flag axes. Update it in
the same PR whenever a flag, backend, domain, loop, runtime environment, or
profiler behavior changes.

## Entry Points

`vibesys` is the single launcher; the same flags below apply no matter how you
start it:

| Entry point | Use |
| --- | --- |
| `vibesys` | The unified launcher (installed). Forwards all flags to the engine. Launches the interactive TUI by default and runs headless with `--headless`, `--help`, `validate`, or when not attached to a TTY. The TUI comes from a prebuilt platform wheel, or is built from source when run inside a checkout (needs Bun + Node 20+ + pnpm). |
| `uv run vibesys` | The same launcher from a source checkout with no install; `uv` provisions the environment. |
| `python -m vibesys` | The headless engine directly. No JavaScript runtime required. |

Examples in this document use `uv run vibesys`; drop the `uv run` prefix when the
`vibesys` command is installed (add `--headless` to skip the TUI).

### Agent configuration

`--config PATH` selects an explicit `agent.toml`. When the flag is omitted,
VibeSys loads `agent.toml` from the process launch working directory if it
exists, otherwise it uses built-in CLI defaults. It does not search parent
directories.

## Mental Model

Several flags look independent, but they combine into one execution contract:

| Axis | Flag | Meaning |
| --- | --- | --- |
| Search loop | `--outer-loop` | Which outer-loop policy runs: `agent`, `plain`, or `evolve`. |
| Evaluation interface | `--interface` | Agent loop only. Whether evaluator-owned code invokes the candidate directly or communicates with a service. |
| Compute backend | `--backend` | Hardware/runtime target: `cuda`, `metal`, `trainium`, `rocm`, or `cpu`. |
| Runtime environment | `--docker`, `--modal` | Where agent commands execute: local shell, Docker container, or Modal-backed workflow. |
| Profiler | `--profiler` | Bottleneck evidence source: `nsys`, `torch`, `neuron`, `otel`, `macos_cpu`, `linux_cpu`, or `auto`. |
| Domain | `[agent].domain` in `vibesys.input.toml` | Problem-space package used by the agent and evolve loops, such as `llm-serving`, `microservices`, or `generic`. |
| Modality | `--modality` | Per-task I/O contract, such as `text_generation` or `speech_to_text`. |
| Skills | `--skills-dir`, `--extra-skills`, `--no-skills` | Override the preset skill roots, stack extra skills on top of the presets, or disable skill loading. |
| Target inputs | `--input` | Complete target project. Defaults to the current directory. |
| State layout | omitted `--runs-dir` or `--runs-dir PATH` | Omit it for an in-place agent run with `.vs/` state. Pass it to create a materialized workspace in an experiment collection. |
| Experiment repository | `--repo`, `--repo-visibility`, `--local`, `--resume` | In-place runs use local branches. Materialized fresh runs use GitHub by default; their resume forms also accept local paths or GitHub repositories. |
| Client theme | `--theme` | Presentation only. Which semantic theme the interactive client renders with. |

Do not treat these as simple toggles. Some combinations imply a startup
contract, profiler, or sandbox capability. Language and artifact requirements
come from the domain and input bundle, not the interface mode.

## Outer Loops

| Value | Behavior | Notes |
| --- | --- | --- |
| `agent` | Orchestrator-driven loop with implementer, judge, and profiler roles. | Default. Supports `--interface` and `--inner-loop`. |
| `plain` | Issue-board loop with deterministic issue draining and perf evaluation. | Uses backend prompt fragments from `src/vibesys/prompts/backend/`. |
| `evolve` | Evolutionary search over candidate implementations. | Uses domain-aware mutator, judge, and profiler roles. |

Run the commands below with `uv run vibesys` (from a checkout) or `vibesys`
(installed). Both forward every argument to the engine and prepare the
interactive client when needed.
Use `vibesys --outer-loop <kind> --help` for loop-specific flags.

## Default In-Place Project Runs

The default agent-loop launch treats the input directory as the working
project. `--input` defaults to the current directory:

```bash
cd /path/to/project
vibesys

# Equivalent without changing directory
vibesys --input /path/to/project
```

The project must contain `OBJECTIVE.md`, `vibesys.input.toml`, and the candidate
source the agent will edit. Its manifest must not declare `[workspace].seed` or
`[[workspace.sources]]`; materialize those starter files into the project first.
It must be the root of its Git repository, or outside any Git repository so
VibeSys can initialize one. A subdirectory of a containing Git repository is
not a valid in-place project root. An existing repository must have a baseline
commit and a clean worktree. A directory outside Git is initialized with a
baseline commit automatically.

In-place execution supports the `agent` outer loop on the local host with a CLI
coding-agent backend and profiler `none` (`auto` resolves to `none`). It does not
copy skill bundles into the project; `--skills-dir` and `--extra-skills` require
`--runs-dir`. Docker, Modal, `plain`, `evolve`, DeepAgents, standalone
`--input-*` synthesis, and active profilers also require `--runs-dir`.

The native sandbox requires `bwrap` on Linux or `sandbox-exec` on macOS. A
missing confinement tool or an unsupported operating system stops the run
rather than launching the agent without isolation. Evaluator, checker, and
benchmark paths are visible to agents but enforced read-only and protected by
integrity checks. The agent also cannot write `.git`, `.vs`, `OBJECTIVE.md`,
`vibesys.input.toml`, `reference/`, or `_evaluator/`, and cannot read
`.vs/local/`, root `.env*` files, or root `agent.toml`. A run is rejected when a
root `.env*` file or `agent.toml` is recoverable from Git refs or reflogs.

VibeSys initializes Git when needed and creates one `vibesys/<run-id>` branch
per run. Agent-authored source stays at its normal project paths. State is split
under `.vs/`:

```text
.vs/
├── .gitignore                         # contains /local/
├── project.json                       # committed project identity
├── runs/<run-id>/
│   ├── run.json                       # committed sanitized run configuration
│   └── rounds/NNNN.json               # committed completed-round records
└── local/                             # ignored operational state
    ├── current-run
    └── runs/<run-id>/
        ├── active.json
        ├── round-transaction.json      # present only during round commit/recovery
        └── logs/
```

The committed files contain portable configuration, fingerprints, metrics, and
round outcomes. They exclude provider credentials, environment variables,
absolute source paths, sessions, and raw logs. Resume restores the saved branch
and run configuration:

```bash
# Resume the run named by .vs/local/current-run, or the newest run if unset
vibesys --resume

# Select a run explicitly
vibesys --resume <run-id>
```

A plain `vibesys` launch starts a new run. It does not silently resume the
current or latest run. When run flags and `--config` are omitted during resume,
their recorded values are restored. Explicit configuration changes are
rejected, except that `--max-rounds` may increase the recorded total. The project
worktree must be clean before VibeSys switches to the saved
`vibesys/<run-id>` branch.

### Agent-loop review and memory policy

| Flag | Default | Behavior |
| --- | ---: | --- |
| `--judge-every N` | `3` | Run an independent judge every Nth round. A candidate explicitly nominated by the implementer and the final round are always reviewed immediately. Canonical accuracy and benchmark commands run only after a judge PASS. |
| `--official-eval-every N` | `3` | Run configured framework-owned accuracy and benchmark gates every N accepted candidate checkpoints. Intermediate checkpoints remain provisional; orchestrator requests and the final round force immediate official evaluation. Retries, continuing hypotheses, and profiler-only rounds do not advance this cadence. Modal gates reuse one healthy deployment for the exact candidate commit, explicitly stop it after the final gate, and rely on zero minimum-warm replicas plus a short finite scaledown window as the crash backstop. Unchanged retries reuse a prior accuracy PASS when only a later gate failed. |
| `--memory-layout` | `files` | `files` keeps `roadmap.md` and `progress.md`. `directories` uses `roadmap/index.md` and one `progress/round-NNNN.md` audit file per round; fresh orchestrators receive a bounded recent window and can inspect older files on demand. Existing runs retain their current layout when resumed. |
| `--constraint TEXT` | none | Add an operator-supplied workload invariant to every agent's objective without changing the input bundle. The framework materializes the effective objective outside candidate Git history and mounts it read-only in isolated environments, so rollback cannot erase it. Repeat for multiple constraints and repeat the same flags when resuming. |

Designer and judge invocations start with clean model sessions. The implementer
session is keyed by the designer's stable `hypothesis_id`, so targeted
experiments and debugging context persist while the causal claim is unchanged.
The designer is not called again while that hypothesis is active. A round
reported as `continue` is provisional when review is not due: neither the judge
nor framework-owned official gates run that round. These independent-judge
cadence rules apply to the default `multi-agent` inner loop; `single-agent` is
retained as an ablation mode.

### Evolve search policies

`--search-policy vibesys` (the default) uses VibeSys's scalar softmax or Pareto
frontier selection. `--search-policy openevolve` imports pinned OpenEvolve 0.3.1
and delegates MAP-Elites archiving, island selection, population limits, and
migration to its `ProgramDatabase`. It does not use OpenEvolve's one-shot LLM
mutation path; VibeSys's coding agent continues to mutate the checked-out
multi-file workspace.

| Flag | Default | Meaning under `--search-policy openevolve` |
| --- | ---: | --- |
| `--openevolve-population-size` | `1000` | Maximum upstream program population. |
| `--openevolve-archive-size` | `100` | Elite archive size. |
| `--openevolve-num-islands` | `5` | Number of island populations sampled round-robin. |
| `--openevolve-migration-interval` | `50` | Per-island admitted generations between migrations. |
| `--openevolve-migration-rate` | `0.1` | Fraction of island elites copied during migration. |

OpenEvolve state is stored under `logs/openevolve/` and loaded on resume. See
[`docs/openevolve.md`](openevolve.md) for the adapter boundary and metric
semantics. On resume the policy and saved settings are restored when these
flags are omitted; partial explicit settings are merged with the saved values,
flag-defined objectives are restored, and incompatible changes are rejected.
On a new run, supplying any
`--openevolve-*` setting also selects the OpenEvolve policy.

## Materialized Experiment Collections and Remote Repositories

Configure interactive defaults in `agent.toml`:

```toml
[repository]
# Optional GitHub user/org override. If omitted, use the account authenticated with `gh`.
# owner = "your-github-user"
visibility = "private"
```

Runs created with `--runs-dir` use GitHub by default. The owner can be any
GitHub user or organization; when `owner` is omitted, VibeSys uses the account
authenticated with `gh`. Interactive and headless launchers pass the same
arguments directly to the engine, so provide the input and collection
explicitly. Names default to `<input-name>-<UTC timestamp>`.

For headless use, the generated repository name is used automatically. Pass
`--repo NAME` to override the name, or `--repo OWNER/NAME` to override the owner
explicitly. This workflow requires `--runs-dir PATH`. Pass `--local` to keep the
experiment in that collection without a GitHub repository. Repositories use
`[repository].visibility` unless `--repo-visibility` overrides it. Creation goes
through the authenticated `gh` CLI.

The experiment repository records the materialized workspace and durable state
needed to continue the loop. Provider/agent `logs/*.log` files and directory
snapshots are excluded. VibeSys commits and pushes after each workspace
checkpoint, then performs a final sync on context shutdown, including normal
loop failures and keyboard interruption. A failed checkpoint push is logged and
retried at the next checkpoint so a transient remote failure does not stop the
optimization run. A final non-fast-forward or authentication failure is reported
rather than force-pushing.

`--resume` accepts collection run identifiers, local experiment directories,
GitHub `OWNER/NAME` pairs, and cloneable HTTPS/SSH URLs:

```bash
uv run vibesys --runs-dir /work/vibesys-runs --resume 20260720-120000-example
uv run vibesys --runs-dir /work/vibesys-runs --resume /work/experiments/example
uv run vibesys --runs-dir /work/vibesys-runs --resume vibesys-playground/example
uv run vibesys --runs-dir /work/vibesys-runs --resume https://github.com/my-org/example.git
```

Remote repositories are cloned into the selected collection. A local clone can
live anywhere, but `--runs-dir` is still required for shared caches and any
future runs. Resumed repositories with an `origin` are synchronized again after
the run. `--repo` only creates a repository for a fresh experiment and cannot be
combined with `--resume`.

## Repository Validation

Run `vibesys validate [INPUT_BUNDLE]` to check an input bundle's static harness
contract without starting the interactive client, an optimization loop, or an
agent. From a source checkout, use the equivalent `uv run vibesys validate` command.

The input bundle is the positional argument. When omitted, it defaults to the
current directory:

```bash
vibesys validate
```

Pass another bundle directly:

```bash
vibesys validate examples/<target>
```

The command applies the same strict schemas and path checks as a real run. It
validates `OBJECTIVE.md`, `vibesys.input.toml`, accuracy and benchmark command
paths, optional workspace seed and evaluator source, and the optional
benchmark-result contract. A valid bundle exits with status 0; an invalid bundle
prints the failing contract and exits with status 1. Command-line usage errors
exit with status 2. Validation does not execute the checker or benchmark.

## Interface

`--interface` applies to the agent loop.

| Value | Process boundary | Contract ownership |
| --- | --- | --- |
| `inprocess` | Evaluator-owned code invokes the candidate directly inside an evaluator process. | The input defines the callable API or ABI, artifacts, ownership, and lifecycle. |
| `service` | Checker and benchmark communicate with a running candidate over its network interface. | The input defines the protocol, endpoints, startup behavior, and artifacts. |

`service` does not automatically rewrite a checker or benchmark. The target
inputs must already know how to probe the running service.

`inprocess` does not imply Python. A Python module imported by an accuracy
checker and a C-ABI shared library loaded by a trusted adapter are both
in-process candidates. Their exact requirements belong to domain/use-case
prompts and input-owned candidate-contract documentation.

## Compute Backends

| Backend | Intended target | Sandbox support | Device handling | Default profiler behavior |
| --- | --- | --- | --- | --- |
| `cuda` | NVIDIA GPU serving systems. | Local, Docker, Modal. | Selects/reselects a GPU and can monitor contention. | Local/Docker use `nsys`; Modal uses `torch` when `--profiler auto`. |
| `metal` | Apple Silicon / MPS targets. | Local only. | No device selection or monitor. | Local `auto` resolves through the local runtime default. |
| `trainium` | AWS Trainium / NeuronCore targets. | Local and Docker; Modal unsupported. | Forwards `/dev/neuron*` in Docker; no per-device selection. | `auto` resolves to `neuron`. |
| `cpu` | CPU-only service/data-structure targets. | Local and Docker. | No device selection or monitor. | Generic workloads on Linux select `linux_cpu`; macOS selects `macos_cpu`; other systems select no profiler. |

When a backend rejects a runtime environment, it should fail before agent work
starts with an actionable error.

## Runtime Environment

| Flags | Environment | Notes |
| --- | --- | --- |
| neither `--docker` nor `--modal` | Local host. | In-place runs require bubblewrap on Linux or Seatbelt on macOS and enforce the project path policy. Materialized workspaces use the host backend without that in-place policy. |
| `--docker` | Docker container. | Mounts the workspace. Backend controls GPU/device passthrough. |
| `--modal` | Modal workflow. | Mutually exclusive with `--docker`. Intended for remote GPU dispatch. |

`--docker-image` overrides the backend's default container image when Docker or
Modal is active.

## Profiler

| Value | Intended use |
| --- | --- |
| `auto` | Let the runtime/backend pick the default profiler. |
| `nsys` | NVIDIA Nsight Systems. Requires a CUDA/NVIDIA profiling environment. |
| `torch` | PyTorch profiler. Used for in-process Python profiling and Modal GPU dispatch. |
| `neuron` | AWS Neuron profiler for Trainium. |
| `otel` | OpenTelemetry service, span, and datastore latency for microservice benchmarks. Opt-in only (`auto` never selects it) and needs an input bundle that provisions instrumentation and a collector. |
| `macos_cpu` | Instruments Time Profiler with a supported `/usr/bin/sample` fallback. |
| `linux_cpu` | Linux `perf` profiler for native and mixed-language CPU workloads. |

`--modal --profiler nsys` is rejected by the CLI because Modal runs must use the
torch profiler path.

Profiler prompts must match the interface, domain, and backend. In-process
execution alone does not make the candidate Python or PyTorch-compatible; the
selected domain must explicitly support Torch profiling. A CPU backend must not
receive a GPU-kernel workflow.

The macOS backend verifies that the selected developer directory is full Xcode and asks
`xctrace` for the Time Profiler template; the Command Line Tools shim is not considered
functional Instruments. Captures are separate diagnostic runs, never scored results.
They store exact commands, duration, warm-up, OS/CPU/tool data, target PID/topology,
diagnostics, and the raw `.trace` or `sample` report. Attach failures, including SIP or
privacy restrictions, are structured diagnostics. Optimized native builds should retain
debug information; `dsymutil`, `dwarfdump`, `nm`, and `atos` can validate or resolve
symbols. Reports must state when unavailable Apple hardware counters limit conclusions.

## Domain and Modality

`[agent].domain` in `vibesys.input.toml` supplies cross-cutting problem-space
context for the agent and evolve loops. Registered domains include:

| Domain | Meaning |
| --- | --- |
| `llm-serving` | LLM-serving guidance, including serving-system skills and judge gates. |
| `microservices` | Microservice workload guidance, lifecycle rules, and service-level evaluation context. |
| `generic` | No extra domain guidance. Useful for custom/non-LLM targets. |

Each input bundle must declare `[agent].domain`; there is no CLI override for a
bundle passed with `--input`. When synthesizing a bundle from `--input-*` flags
instead, `--input-domain` sets `[agent].domain` for the generated manifest. New
domains are added in source by registering a domain package with optional
environment setup/teardown hooks.

`--modality` supplies the task I/O contract, such as text generation or
speech-to-text. Domains and modalities may define language, toolchain, and
artifact requirements. Interface-specific prose should describe only the
direct-call or service boundary.

## Client Theme

`--theme` selects the semantic theme the interactive client renders with. It is
presentation only: it never reaches the agents, the workspace, or the recorded
run state, and it is ignored in headless mode.

| Theme | Appearance | Use for |
| --- | --- | --- |
| `dark` | dark | Default dark palette. |
| `light` | light | The baseline palette inverted for light terminals. |
| `solarized-dark` | dark | Low-glare Solarized palette. |
| `solarized-light` | light | Low-glare Solarized palette. |
| `catppuccin-mocha` | dark | Softer, more expressive palette. |
| `catppuccin-latte` | light | Softer, more expressive palette. |
| `high-contrast-dark` | dark | Accessibility-focused; every foreground clears a 7:1 contrast ratio. |
| `high-contrast-light` | light | Accessibility-focused; every foreground clears a 7:1 contrast ratio. |

Resolution order, highest first:

1. `--theme <name>` on the command line.
2. `dark`.

An unknown name is rejected before any process starts. Inside a running
session, `/theme` lists the available themes and `/theme <name>` switches
immediately; that switch applies to the session only and does not edit
`agent.toml`.

Themes define semantic roles — surfaces, text emphasis levels, borders,
accents, status colors, conversation roles, and Markdown/code colors — rather
than per-component colors, and every derived foreground is checked against its
own background at build time. Status is never carried by color alone: agent
phases show a marker glyph and the spelled-out status, todo items show a
per-status marker, and the running round is the only one with an elapsed-time
suffix.

```bash
uv run vibesys --runs-dir /work/vibesys-runs --local \
  --input examples/model-serving/Llama-3-8B --theme solarized-light
```

## Skills

Skill sources come from two flags, both repeatable, and each value may point at
one skill directory containing `SKILL.md`, a parent tree containing multiple
skills, or a single `SKILL.md` file:

- **`--skills-dir PATH`** *replaces* the built-in preset roots. When omitted, the
  preset `resources/skills/` is the base.
- **`--extra-skills PATH`** *stacks on top of* the presets (or on top of
  `--skills-dir` when that is given). Use this to add your own skills while
  keeping the presets such as the `llm-serving` serving-systems skills. A
  same-named skill from `--extra-skills` overrides a preset one.

```bash
# presets + your own skill directory and a single SKILL.md file
uv run vibesys --runs-dir /work/vibesys-runs --local --input <bundle> \
  --extra-skills ./my-skills \
  --extra-skills ./one-off-skill/SKILL.md

# use ONLY your skills, ignoring the presets
uv run vibesys --runs-dir /work/vibesys-runs --local \
  --input <bundle> --skills-dir ./my-skills
```

Before a run starts, VibeSys discovers each `SKILL.md` under the candidate
roots and validates its frontmatter. Optional `.vibesys.toml` sidecars can
declare domain and backend applicability for a skill subtree:

```toml
[[rule]]
path = "skills"
backends = ["trainium"]
domains = ["llm-serving"]
```

Effective skill loading is the intersection of the declared constraints:

- unscoped skills load for every domain and `--backend`;
- skills matched by a sidecar rule with `backends` load only when the selected
  backend is in that list;
- skills matched by a sidecar rule with `domains` load only when the input
  bundle's `[agent].domain` is in that list;
- `--skills-dir` and `--extra-skills` add candidate roots, but routing metadata
  still filters the discovered skills;
- `--no-skills` disables all skill loading, including scoped skills, and
  overrides both `--skills-dir` and `--extra-skills`.

See [Skill Metadata](skill-metadata.md) for the VibeSys-specific metadata
contract and validation rules.

## Target Inputs

Most examples use the standard bundle layout:

```text
examples/<target>/
├── OBJECTIVE.md
├── vibesys.input.toml
├── reference/              # optional reference or seed inputs
├── accuracy_checker/       # optional local checker; may be evaluator-owned
└── benchmark/              # local or evaluator-owned benchmark entrypoint
```

For nontrivial callable APIs, ABIs, ownership rules, or service protocols, keep
the normative implementation requirements in `CANDIDATE_CONTRACT.md` and link
to it from `OBJECTIVE.md`. A shared evaluator may own this file when several
input bundles use exactly the same contract. Keep evaluator internals and trust
assumptions in a separate design document.

For a complete in-place project, launch from its root or pass the root once:

```bash
cd /path/to/project
vibesys ...

vibesys --input /path/to/project ...
```

Checked-in examples are nested below the VibeSys repository root, so run them
in a materialized local experiment collection:

```bash
uv run vibesys --runs-dir /work/vibesys-runs --local --input examples/<target> ...
```

The same mode materializes separate starter sources declared through
`[workspace].seed` or `[[workspace.sources]]`.

The manifest declares the evaluator entrypoints and does not define a candidate
command:

```toml
version = 1

[agent]
domain = "generic"

[accuracy]
command = ["uv", "run", "python", "accuracy_checker/checker.py"]

[benchmark]
command = ["uv", "run", "python", "benchmark/benchmark.py"]

[workspace]
seed = "../../starters/example-rust-candidate"

[evaluator]
source = "../../evaluators/example"

[benchmark.result]
json_argument = "--output-json"
metric = "requests_per_second"
```

Those command arrays are bundle-specific. They may point at Python, shell, Go,
Rust, C++, or any other evaluator entrypoint, and VibeSys does not require
standard wrapper filenames. VibeSys copies the input bundle into the
experiment workspace and tells agents to run the manifest commands. The
optional `benchmark.result` block opts a single-metric benchmark into trusted
framework scoring: VibeSys appends `json_argument`, reads the resulting JSON,
and requires a finite numeric field named by `metric`. For a JSON object, that
field must be at the top level and is authoritative even when per-trial
diagnostics repeat the same name. List-shaped results are accepted when they
contain exactly one field with that name. Omit the result block for
multi-profile or multi-objective benchmarks whose result cannot be represented
by one scalar. Named profiles and benchmark parameter schemas are not part of
manifest version 1.

The optional `workspace.seed` path is relative to the input manifest and must
resolve inside the repository's `examples/starters/` directory. On a fresh run,
VibeSys copies non-ignored seed files first and then copies the input bundle.
Any top-level path supplied by both sources is rejected instead of being
overwritten. The resulting files are ordinary candidate workspace files: agents
may edit or delete them, and resumed runs never refresh them from the seed.

The optional `evaluator.source` path is relative to the input manifest and must
resolve inside `examples/evaluators/`. On a fresh run, VibeSys copies it to
`_evaluator/<source-name>`. This is a separate, evaluator-owned input: Git-backed
integrity checks reject accuracy and benchmark gates after it is modified.
Resumed runs keep the evaluator snapshot from the original run instead of
refreshing it from repository source.

To intentionally upgrade evaluator-owned inputs in an existing experiment,
commit the authorized refresh before resuming and pass that immutable revision.
This applies only to materialized workspaces created with `--runs-dir`;
in-place project runs persist their original baseline and reject this flag.

```bash
uv run vibesys --outer-loop agent \
  --runs-dir /work/vibesys-runs \
  --resume /path/to/experiment \
  --trusted-input-baseline <refresh-commit>
```

The revision must be an ancestor of the current experiment `HEAD`. The guard
continues to reject pending trusted-input edits and every trusted-input change
committed after that baseline. Omitting the flag retains the original initial
workspace baseline, so ordinary resumes cannot silently bless agent tampering.

### Providing inputs without a bundle (`--input-*`)

For external usage where no `examples/` bundle is on disk, pass the bundle's
contents as separate `--input-*` flags instead of `--input`. VibeSys synthesizes
a bundle under `<runs-dir>/_inputs/<exp-name>/` and then loads it through the
same path as `--input`, so every loop, resume, and evaluator behaves identically.
The two forms are mutually exclusive: combining `--input` with any `--input-*`
flag is rejected.

Required flags:

| Flag | Maps to |
| --- | --- |
| `--input-objective TEXT` or `--input-objective-file PATH` | `OBJECTIVE.md` |
| `--input-domain {llm-serving,generic,microservices}` | `[agent].domain` |
| `--input-accuracy-command CMD` | `[accuracy].command` (shell-quoted argv) |
| `--input-benchmark-command CMD` | `[benchmark].command` (shell-quoted argv) |

Optional flags:

| Flag | Maps to |
| --- | --- |
| `--input-accuracy-timeout SECONDS` / `--input-benchmark-timeout SECONDS` | command `timeout_seconds` |
| `--input-benchmark-metric NAME` + `--input-benchmark-result-arg OPT` | `[benchmark.result]` (both required together) |
| `--input-reference DIR` | copied to `reference/` |
| `--input-evaluator-dir DIR` | contents copied into the bundle root (evaluator scripts the commands invoke) |
| `--input-workspace-seed DIR` | `[workspace].seed` (staged inside the bundle) |
| `--input-evaluator-source DIR` | `[evaluator].source` (staged inside the bundle) |

Unlike bundle-declared `workspace.seed` and `evaluator.source`, which must
resolve inside `examples/starters/` and `examples/evaluators/`, the synthesized
bundle stages these directories inside itself, so any local directory is
accepted. Git-pinned `[[workspace.sources]]` entries are not exposed as flags;
use `--input` for those.

```bash
uv run vibesys \
  --runs-dir /work/vibesys-runs \
  --input-objective-file ./OBJECTIVE.md \
  --input-domain llm-serving \
  --input-accuracy-command "python checker.py" \
  --input-benchmark-command "python benchmark.py --result-json out.json" \
  --input-benchmark-metric requests_per_second \
  --input-benchmark-result-arg --result-json \
  --input-evaluator-dir ./evaluator \
  --local
```

## Common Commands

Default agent loop on local CUDA-compatible host:

```bash
uv run vibesys \
  --runs-dir /work/vibesys-runs \
  --local \
  --outer-loop agent \
  --backend cuda \
  --interface inprocess \
  --input examples/model-serving/Llama-3-8B
```

Docker CUDA run:

```bash
uv run vibesys --runs-dir /work/vibesys-runs --local \
  --outer-loop agent --backend cuda --docker ...
```

Modal GPU run:

```bash
uv run vibesys --runs-dir /work/vibesys-runs --local \
  --outer-loop agent --backend cuda --modal --profiler torch ...
```

Trainium run:

```bash
uv run vibesys --runs-dir /work/vibesys-runs --local \
  --outer-loop agent --backend trainium --profiler auto ...
```

Over-the-wire service target:

```bash
uv run vibesys \
  --runs-dir /work/vibesys-runs \
  --local \
  --outer-loop agent \
  --interface service \
  --input examples/<target>
```

CPU-only target:

```bash
uv run vibesys --runs-dir /work/vibesys-runs --local \
  --outer-loop agent --backend cpu --interface service ...
```

CPU runs support local execution and Docker; use local execution unless you
specifically need the container boundary.

## Maintenance Rule

When adding or changing a flag:

1. Update this document.
2. Add or update validation for unsupported combinations.
3. Add prompt-rendering tests for combinations that change generated
   instructions.
4. Keep README focused on quickstart guidance and link here for details.
