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
- **Execution environment** — an isolated workspace that mounts the user-provided artifacts read-only (so the Implementer cannot edit the checker or reference) and exposes the target hardware (local CUDA, Modal, Docker, or Apple Silicon) plus profilers.

Each round is recorded in git and a framework-owned audit. Provisional rounds
remain explicitly unreviewed; only judge-approved candidates receive official
accuracy and performance results.

## Installation

1. Install Python 3.12+ and [uv](https://docs.astral.sh/uv/).
2. For the default GitHub-synced runs, install the [GitHub CLI](https://cli.github.com/)
   and sign in with `gh auth login`. You do not need `gh` when every run uses
   `--local`.
3. From the repository root, create the local configuration files:

```bash
cp .env.example .env       # provider keys for API-backed/deepagents runs
cp agent.toml.example agent.toml
```

### Coding-agent setup

Choose one of these agent options in `agent.toml` or with command-line flags:

| Agent | Selection | Authentication |
| --- | --- | --- |
| Codex CLI | `--agent-backend cli --cli-provider codex` | Install Codex and run `codex login`. |
| Claude Code | `--agent-backend cli --cli-provider claude` | Install Claude Code and use its login flow. |
| Gemini CLI | `--agent-backend cli --cli-provider gemini` | Install Gemini CLI and use its login flow. |
| OpenCode | `--agent-backend cli --cli-provider opencode` | Install OpenCode and configure its provider. |
| DeepAgents | `--agent-backend deepagents` | Put the selected provider's API credentials in `.env`. |

The default `agent.toml.example` selects the Codex CLI. CLI credentials stay
with the CLI; API credentials are loaded from `.env` automatically.

3. Check the installation:

```bash
./vs validate examples/data-structures/queue-spsc
```

`uv run` creates the Python environment automatically. You do not need to run
`uv sync` first.

To use the interactive TUI, install Node.js 20+, Bun, and pnpm 11 (or enable
Corepack). Run `./vs`; it installs the frontend dependencies and builds the TUI
when needed. npm is not required.

## Quickstart

```bash
# Issue-tracker outer loop, Codex CLI, Docker on local CUDA, 4 rounds
./vs \
  --input examples/model-serving/moonshine-streaming \
  --exp-name my-experiment \
  --docker \
  --agent-backend cli --cli-provider codex \
  --max-rounds 4 \
  --modality speech_to_text
```

`--outer-loop` defaults to `agent`. Pass `--outer-loop plain` or `--outer-loop evolve` to switch. See `./vs --outer-loop <kind> --help` for loop-specific flags, and [`docs/cli-flags.md`](docs/cli-flags.md) for the supported flag combinations.

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

## Per-target inputs

Each evaluation target lives under `examples/<name>/`:

```
examples/<name>/
├── OBJECTIVE.md          # free-form deployment goal (model + hardware + workload + interface)
├── vibesys.input.toml  # manifest-declared domain, checker, benchmark, and optional inputs
├── reference/            # optional reference or seed inputs
│   ├── reference.py
│   ├── config.json
│   └── meta.json         # model id + revision
├── accuracy_checker/     # optional checker.py + tests/data — the correctness gate
├── benchmark/            # benchmark.py + load levels — emits the metric to optimize
└── README.md             # human-readable description
```

Pass the bundle root with `--input examples/<name>/`. The manifest declares the
agent domain, correctness command, benchmark command, optional starter
workspace, optional trusted evaluator source, and optional benchmark result
metric.

`OBJECTIVE.md` is read at the start of every run and must live next to the
`reference/` directory (sibling, not inside). See `examples/model-serving/Llama-3-8B/`, `examples/model-serving/moonshine-streaming/`, `examples/model-serving/qwen3-32b-code-edit/`, `examples/model-serving/olmo-hybrid-prefix-caching/`, `examples/model-serving/Llama-3.1-8B-Instruct-MLX-8bit/`, `examples/model-serving/show-o2-1.5B-HQ-h100/`, and `examples/model-serving/show-o2-1.5B-HQ-macbook/` for the paper scenarios.

For multi-objective evolutionary runs, drop an `objectives.toml` next to `OBJECTIVE.md` (or pass `--objective name:max|min` flags) — see `./vs --outer-loop evolve --help`.

## Configuration (`agent.toml`)

```toml
[model]
name = "gpt-5.4"             # auto-detected provider for claude-* / gpt-* / gemini-* / gemma-*
# provider = "openai"        # optional override

[backend]
name = "cuda"                 # or "metal", "trainium", "rocm", or "cpu"

[agent]
backend = "cli"               # "cli" (codex/claude/gemini/opencode) or "deepagents"
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

Provider credentials live in `.env` — see `.env.example`. The CLI flags `--agent-backend` / `--cli-provider` / `--backend` override these.

The config is validated against a typed schema on load (`vibesys/config.py`): unknown sections or keys, unknown providers/backends, and missing required fields are rejected with an error rather than silently ignored.

## Outputs

Every run creates `exp_env/<timestamp>-<name>/`:

```
exp_env/<run>/
├── workspace/                # unified, git-tracked candidate workspace
│   ├── roadmap.md            # or roadmap/index.md
│   └── progress.md           # or progress/round-NNNN.md
├── logs/
│   ├── run-*.log             # top-level run log
│   ├── run-*-roundNNN.log    # per-round agent log (agent loop)
│   ├── rounds.json           # per-round audit
│   ├── active_hypothesis.json # resumable implementer handoff, while active
│   ├── state.json            # cursor (plain loop)
│   ├── issues.json           # IssueBoard (plain loop)
│   ├── population.json       # Individual list (evolve loop)
│   └── docker.log
```

Resume any run with `--resume` (defaults to "latest"):

```bash
./vs --resume                  # newest run
./vs --resume 20260507-...     # specific dir
./vs --resume /path/to/clone   # local experiment clone
./vs --resume owner/repo       # clone a GitHub experiment, then resume it
```

Fresh runs use GitHub by default. The interactive setup form is populated with
the input path, an automatically generated experiment name, the configured
repository owner (or the account from `gh`), repository name, and visibility.
Use Tab/Shift-Tab to move through the defaults and Enter to launch. Clear the
owner field, or pass `--local`, to keep a run under `exp_env/` without creating
or syncing a GitHub repository.

```bash
gh auth login
./vs \
  --input examples/data-structures/queue-spsc \
  ...
```

For non-interactive use, the repository name is generated from the input bundle;
`--repo queue-trial` overrides the name, and `--repo another-org/queue-trial`
overrides the owner explicitly. Durable workspace and run state are committed
and pushed after each workspace checkpoint and again when the run closes; raw
`logs/*.log` files stay local. Checkpoint push failures are retried without
stopping the run. Later runs automatically push again when resumed from a clone
with an `origin`.

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
