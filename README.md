# VibeSys: Generating Bespoke Systems with AI Agents

[![arXiv](https://img.shields.io/badge/arXiv-2605.06068-b31b1b.svg)](https://arxiv.org/abs/2605.06068)

**An agentic framework that generates bespoke systems from application requirements, workload characteristics, and the underlying hardware.**

One of VibeSys's first initiatives is **VibeServe**, which asks whether AI agents
can generate a bespoke LLM serving system for each model, workload, and hardware
target. The figures, blog post, and paper below document that initiative.

<p align="center">
  <img src="docs/figures/idea.png" width="85%" alt="Generic serving today vs. VibeServe's per-target bespoke systems">
</p>

## Updates

- **2026-05** — VibeServe blog post: [Let AI Agents Write Your Serving Stack with VibeServe](https://syfi.cs.washington.edu/blog/2026-05-12-introducing-vibeserve/).
- **2026-05** — Paper released on arXiv: [2605.06068](https://arxiv.org/abs/2605.06068).

## Introduction

VibeSys explores a broader approach to systems development: use application
requirements, workload characteristics, and hardware capabilities as the inputs
to an agentic search process that creates a purpose-built system. Each target
defines its own implementation contract, correctness checks, and performance
benchmark, allowing VibeSys to work across domains rather than assuming a single
runtime, programming language, or deployment shape.

The framework is organized as a multi-agent optimization loop. An outer loop
plans the search over system designs using persistent state such as issues,
memory, and git history, while an inner loop implements candidates, validates
correctness against target-specific requirements, and measures performance on
the target workload and hardware. VibeServe is the first substantial initiative
built on this approach; its serving-focused results include predicted-output
decoding, hybrid prompt caching, streaming ASR, constrained JSON decoding,
multimodal inference, and Apple Silicon deployment.

## Architecture

<p align="center">
  <img src="docs/figures/architecture.png" width="90%" alt="VibeServe architecture: outer loop dispatches per-round tasks to an inner loop of Implementer / Accuracy Judge / Performance Evaluator agents">
</p>

The framework factors the work along two axes:

- **Outer loop** — a fresh designer selects one falsifiable causal hypothesis
  from git history, profiling evidence, and durable roadmap/progress memory, then
  hands it off until it is proven, disproven, or otherwise terminated.
- **Inner loop** — a hypothesis-scoped implementer session edits the candidate,
  chooses targeted experiments and parameter ranges, and reports whether to
  continue or nominate the result.
- **Independent judge** — a fresh, read-only reviewer checks the implementation,
  activation evidence, invariants, and reward-hacking risks at a sparse cadence.
  After a PASS, the framework—not an agent—runs and records the canonical
  accuracy and benchmark commands.
- **Performance evaluator** — profiles the implementation (Nsight Systems,
  PyTorch profiler) and feeds bottleneck hints into future design decisions.
- **Skills library** — Agent Skills entries distilled from existing serving engines and research literature (continuous batching, paged-KV, FlashInfer/FlashAttention, MLX, hybrid-cache management, …). New model families, hardware platforms, and optimization techniques are added by writing a skill, not by modifying the framework.
- **Execution environment** — an isolated workspace where candidate source is
  writable while evaluator-owned inputs and framework metadata are read-only
  and integrity-checked. It exposes the target hardware (local CUDA, Modal,
  Docker, or Apple Silicon) plus profilers.

Each round is recorded in git and a framework-owned audit. Provisional rounds
remain explicitly unreviewed; only judge-approved candidates receive official
accuracy and performance results.

## Installation

Install Python 3.12+, Git, and [uv](https://docs.astral.sh/uv/), then install the
published package:

```bash
uv tool install vibesys
```

### Coding-agent setup

Install and authenticate one of the supported coding-agent CLIs:

| Agent | Selection | Authentication |
| --- | --- | --- |
| Codex CLI | `--agent-backend cli --cli-provider codex` | Install Codex and run `codex login`. |
| Claude Code | `--agent-backend cli --cli-provider claude` | Install Claude Code and use its login flow. |
| Gemini CLI | `--agent-backend cli --cli-provider gemini` | Install Gemini CLI and use its login flow. |
| OpenCode | `--agent-backend cli --cli-provider opencode` | Install OpenCode and configure its provider. |

CLI credentials stay with the CLI.
Materialized workspaces created with `--runs-dir` also support DeepAgents via
`--agent-backend deepagents`; export its provider credentials, such as
`OPENAI_API_KEY`, or load them through your own configuration.

Run VibeSys from a complete project containing its candidate source,
`OBJECTIVE.md`, and `vibesys.input.toml`:

```bash
cd /path/to/my-project
vibesys validate
vibesys --backend cpu --max-rounds 4
```

The project directory must be its Git repository root, or outside any existing
Git repository so VibeSys can initialize one. An existing repository needs a
baseline commit and a clean worktree. On Linux, install `bubblewrap`; macOS uses
the system `sandbox-exec` command.

The published wheel includes the interactive TUI for Linux x86-64, Linux ARM64,
macOS Intel, and macOS Apple Silicon. No separate JavaScript runtime is needed.
Use `--headless` to run without the TUI. Install the selected coding-agent CLI
separately. Bring a complete project; starter bundles are not included in the
installed package.

`agent.toml` is optional. When `--config` is omitted, VibeSys loads
`./agent.toml` from the launch directory if present, otherwise it uses built-in
defaults. Pass `--config /path/to/agent.toml` to select another file.

The [GitHub CLI](https://cli.github.com/) is only needed when an experiment
collection created with `--runs-dir` should sync to GitHub. Use `--local` to
keep that collection on the local machine.

The `vibesys` command accepts the same run flags described in
[`docs/cli-flags.md`](docs/cli-flags.md). The default agent-loop workflow runs
directly in a complete project and stores its state under `.vs/`.

```bash
# Run from a complete project containing source, OBJECTIVE.md, and vibesys.input.toml
cd <project>
vibesys --agent-backend cli --cli-provider codex

# Equivalent explicit input in headless mode
vibesys --headless --input <project> --agent-backend cli --cli-provider codex

vibesys --help                    # full flag list
```

`vibesys` also runs headless automatically for `validate` and when stdin/stdout
is not a TTY (pipes, CI).

In-place runs keep agent-authored source in the project root. Git records code
evolution on a `vibesys/<run-id>` branch. Portable completed-run metadata lives
in committed `.vs/project.json` and `.vs/runs/<run-id>/`; `.vs/local/` contains
logs, active state, and the current-run pointer and is ignored by `.vs/.gitignore`.
Use `vibesys --resume` for the current run or `vibesys --resume <run-id>` to
select a run explicitly.

`--runs-dir PATH` selects materialized-workspace mode and stores experiments in
the given collection. Use it for `plain` and `evolve`, standalone `--input-*`
synthesis, Docker or Modal execution, seed/source materialization, and remote
experiment repositories.

## Quickstart

```bash
# A complete project already contains the candidate source and evaluator contract
cd /path/to/my-project
vibesys --backend cpu --max-rounds 4

# Launch the same mode without changing directories
vibesys --input /path/to/my-project --backend cpu --max-rounds 4
```

Input manifests used in-place must not declare `[workspace].seed` or
`[[workspace.sources]]`; materialize the starter source into the project first.

`--outer-loop` defaults to `agent`. Pass `--outer-loop plain` or `--outer-loop evolve` to switch. See `vibesys --outer-loop <kind> --help` for loop-specific flags, and [`docs/cli-flags.md`](docs/cli-flags.md) for the supported flag combinations.

## Search strategies

VibeSys supports three outer-loop search strategies:

- `agent` (default): an orchestrator plans each round and delegates to the
  implementer, judge, and profiler. It uses the `multi-agent` inner loop by
  default; pass `--inner-loop single-agent` to run the single-agent ablation.
- `evolve`: an evolutionary search over candidate implementations.
- `plain`: an issue-board loop that drains implementation issues and evaluates
  performance.

For contributor workflows and extension guides, see
[`docs/development.md`](docs/development.md).

## Project layout

A complete project keeps candidate source and its evaluation contract together:

```
project/
├── OBJECTIVE.md          # deployment goal, workload, hardware, and interface
├── vibesys.input.toml    # domain, checker, benchmark, and optional inputs
├── <candidate source>    # files the optimization agent edits
├── reference/            # optional reference or seed inputs
│   ├── reference.py
│   ├── config.json
│   └── meta.json         # model id + revision
├── accuracy_checker/     # optional correctness gate
├── benchmark/            # benchmark that emits the metric to optimize
├── _evaluator/           # optional evaluator-owned support code
└── README.md             # human-readable description
```

Launch from the root of a complete project or pass that root with `--input`.
For default in-place execution, the project root also contains the candidate
source and must be the Git repository root (or outside any existing Git
repository so VibeSys can initialize one).
The manifest declares the
agent domain, correctness command, benchmark command, optional starter
workspace, optional evaluator source, and optional benchmark result
metric.

Evaluator, checker, and benchmark files are visible to agents but read-only and
integrity-checked during in-place runs.

For external usage without a bundle on disk, supply the same pieces as separate
`--input-objective`/`--input-objective-file`, `--input-domain`,
`--input-accuracy-command`, and `--input-benchmark-command` flags (plus optional
`--input-reference`, `--input-evaluator-dir`, and others) together with
`--runs-dir`. VibeSys synthesizes a bundle and runs it identically. See
[`docs/cli-flags.md`](docs/cli-flags.md#providing-inputs-without-a-bundle---input-)
for the full flag list.

`OBJECTIVE.md` is read at the start of every run and must live next to the
`reference/` directory (sibling, not inside).

For multi-objective evolutionary runs, drop an `objectives.toml` next to `OBJECTIVE.md` (or pass `--objective name:max|min` flags) — see `vibesys --outer-loop evolve --help`.

## Optional configuration (`agent.toml`)

```toml
[model]
name = "gpt-5.4"             # auto-detected provider for claude-* / gpt-* / gemini-* / gemma-*
# provider = "openai"        # optional override

[backend]
name = "cuda"                 # or "metal", "trainium", "rocm", or "cpu"

[agent]
backend = "cli"               # in-place; "deepagents" is available with --runs-dir
cli_provider = "codex"        # which coding-agent harness to drive
# cli_timeout = 1800          # per-invocation timeout (seconds)

# Optional role-specific CLI models. Other roles, including the independent
# judge, continue to use [model].name and [thinking].level.
[agent.outer]
model = "gpt-5.6-sol"         # orchestrator pre-round and planning calls
reasoning_effort = "xhigh"

[agent.inner]
model = "gpt-5.6-luna"        # implementer calls
reasoning_effort = "xhigh"

[repository]
# Optional GitHub user/org override. If omitted, use the account from `gh auth status`.
# owner = "your-github-user"
visibility = "private"        # private, public, or internal

# Optional: benchmark load levels handed to the perf evaluator.
# [[perf_eval.load_levels]]
# rate = 1
# duration = 20
# max_tokens = 128
```

Provider credentials can be exported or placed in a root `.env`. The CLI flags
`--agent-backend`, `--cli-provider`, and `--backend` override these settings.

The interactive client ships four light/dark theme pairs: `dark` (default) /
`light`, `solarized-dark` / `solarized-light`, `catppuccin-mocha` /
`catppuccin-latte`, and `high-contrast-dark` / `high-contrast-light`. Set one
with `--theme`, or switch mid-session with `/theme <name>`.
See [`docs/cli-flags.md`](docs/cli-flags.md#client-theme).

The config is validated against a typed schema on load (`vibesys/config.py`): unknown sections or keys, unknown providers/backends, and missing required fields are rejected with an error rather than silently ignored.

Fresh in-place runs stay local and use a `vibesys/<run-id>` branch. Experiment
collections created with `--runs-dir` use GitHub-backed tracking by default;
pass `--local` to keep an experiment local. See
[`docs/cli-flags.md`](docs/cli-flags.md) for the full repository and resume
contracts.

## Citation

If you use the VibeServe initiative in your research, please cite:

```bibtex
@misc{kamahori2026vibeserveaiagentsbuild,
      title={VibeServe: Can AI Agents Build Bespoke LLM Serving Systems?},
      author={Keisuke Kamahori and Shihang Li and Simon Peter and Baris Kasikci},
      year={2026},
      eprint={2605.06068},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2605.06068},
}
```
