# VibeSys: Generating Bespoke Systems with AI Agents

[![arXiv](https://img.shields.io/badge/arXiv-2605.06068-b31b1b.svg)](https://arxiv.org/abs/2605.06068)

**An agentic framework that generates bespoke systems from application requirements, workload characteristics, and the underlying hardware.**

## Installation

Install Python 3.12+, Git, and [uv](https://docs.astral.sh/uv/). Linux also
requires `bubblewrap`; macOS includes the required `sandbox-exec` command.

```bash
uv tool install vibesys
```

VibeSys drives a coding-agent CLI that is already installed and authenticated.
This quickstart uses Codex CLI: install it and run `codex login`. Claude Code,
Gemini CLI, and OpenCode are also supported; see the
[CLI reference](docs/cli-flags.md).

## Quickstart

Start with the project you want to optimize, including its source and evaluation
programs. VibeSys requires only two files at the project root: `OBJECTIVE.md`
and `vibesys.input.toml`. It does not prescribe the rest of the project layout.

Describe the goal and constraints in `OBJECTIVE.md`:

```markdown
# Objective

Optimize this SPSC queue for maximum throughput. Preserve its public API and
pass the configured correctness check.
```

Point VibeSys at the correctness check and benchmark in `vibesys.input.toml`:

```toml
version = 1

[agent]
domain = "generic"

# This can be any directory containing project-local evaluator code.
[evaluator]
source = "evaluation"

[accuracy]
command = ["python", "evaluation/check.py"]

[benchmark]
command = ["python", "evaluation/benchmark.py"]

[benchmark.result]
json_argument = "--output-json"
metric = "throughput"
```

The accuracy command passes when it exits with status 0. VibeSys appends
`--output-json <path>` to the benchmark command, which must exit with status 0
and write a finite number such as `{"throughput": 123.4}` to that path. The
metric should be a higher-is-better score. The optional `[evaluator]` entry
makes project-local evaluator code read-only; omit it when the commands use
tools installed outside the project.

Run from the project root. It must be the Git repository root, or outside Git
so VibeSys can initialize a repository. An existing repository needs a baseline
commit and a clean worktree.

```bash
cd /path/to/my-project
vibesys validate
vibesys --cli-provider codex --backend cpu --max-rounds 4
```

VibeSys edits the project on a `vibesys/<run-id>` branch and stores run state in
`.vs/`. Use `vibesys --resume` to continue the latest run. `agent.toml` is
optional and only needed for persistent runtime overrides. See
[`docs/cli-flags.md`](docs/cli-flags.md) for other coding agents, hardware
backends, explicit input paths, headless execution, resume behavior, and
advanced run modes. Contributor setup belongs in
[`docs/development.md`](docs/development.md).

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
