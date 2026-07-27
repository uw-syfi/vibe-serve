Model weights are at `/model` — do NOT download models.

## Python toolchain

Use `uv` for Python package management. Run `uv init` if `pyproject.toml`
doesn't exist yet, and `uv add` for new dependencies. Always execute Python
scripts via `uv run`.

The Judge also runs a standard accuracy check and benchmark sanity test in addition to this round's pass criteria. Your implementation must pass those too.

## Required: read the relevant skill BEFORE writing code

The `serving-systems` skill is installed in your working directory with a `references/` library covering every kernel, library, algorithm, and technique relevant to this work. **You must consult the relevant references before you write any code that touches them. This is not optional.**

The references library lives at `references/<tier>/<topic>.md` (the `serving-systems` skill's `SKILL.md` body is the index). Tiers: `algorithms`, `frameworks`, `models`, `engines`, `tooling`, and `platforms/<backend>`.

**Start at `references/platforms/`.** Exactly one backend's directory is present — the one this run targets — and its `floor.md` is the optimization floor for this hardware. The floor is *not* the same across backends: eliminating KV padding is correct on `cuda` and inverted on `trainium`, and graph capture does not exist on `metal`. Read your platform's floor before applying any technique you know from elsewhere.

Portable `algorithms/` files state the contract — the invariants any implementation must satisfy. Where the technique differs by hardware, the contract points into `references/platforms/` for the implementation. Read the contract first, then the platform file.

**Before writing or modifying code, open every reference that covers a topic named in the task.** Some examples — these are not exhaustive:

- Task says "graph capture" / "graph replay" / "CUDA graphs" → open your platform's `references/platforms/*/` notes; on `cuda` that is `cuda-graph.md`. Some backends have no capture step at all.
- Task says "FlashAttention" / "FlashInfer" / "swap attention backend" / "fused attention" → open `references/platforms/*/floor.md` for the backend's fused-attention answer; on `cuda` start from `attention-backend-comparison.md` (the picker), then the per-backend reference (`flashattention.md`, `flashinfer.md`, or `sdpa.md`) for whichever you commit to.
- Task says "EAGLE3" / "spec decoding" / "draft model" / "MTP" → open `references/algorithms/speculative-decoding.md` *thoroughly*, then your platform's implementation. Read the section on draft-vocab-to-target mapping (`d2t`/`t2d`) and the auxiliary-hidden-state handoff before you write a single line — those two failure modes alone are responsible for most "EAGLE3 wired but 0 acceptance" outcomes in this loop.
- Task says "xgrammar" / "structured output" / "JSON schema" / "grammar mask" → open `references/algorithms/structured-output.md`.
- Task says "paged attention" / "block table" / "KV cache pages" → open `references/algorithms/paged-attention.md`. Note its N/A rows — not every backend has a discrete memory pool to page.
- Task says "continuous batching" / "scheduler" → open `references/algorithms/continuous-batching.md` (the contract), then your platform's `continuous-batching.md`. The KV strategy inverts between backends.
- Task says "torch.compile" / "PyTorch idioms" → open `references/frameworks/pytorch.md`.
- Task says "nsys" / "Nsight" / "torch profiler" / "where is the time going" → open `references/tooling/profiler.md` for the discipline, then your platform's `profiler.md` for the toolchain.

**Coding from priors is the single most common reason this loop wastes rounds.** Concrete failure modes already observed:

- Implementer wrote SDPA-only attention for 24 rounds because no one opened the platform's fused-attention reference — leaving 3-5× perf on the table.
- Implementer wired EAGLE3 with 0 acceptance and abandoned it, because no one read the `d2t`/`t2d` section of `references/algorithms/speculative-decoding.md` — leaving another 2× on the table.
- Implementer guessed graph-capture semantics, ran into "fixed-shape mask" bugs, abandoned the attempt — the platform's graph-capture reference covers exactly those bugs.
- Implementer applied a technique that is correct on a different backend. The platform `floor.md` exists to prevent this; read it before porting anything you know from another accelerator.

**Process this round, in order:**

1. Read the `serving-systems` skill's `SKILL.md` body (the router) if you haven't already, to know which references exist.
2. Read `references/platforms/<backend>/floor.md` for the one platform directory present. This is the optimization floor for this hardware and it differs by backend.
3. For every kernel / library / algorithm named in this round's task, open the corresponding `references/<tier>/<topic>.md`. Skim is fine; cover-to-cover only when the task is structural.
4. **In your `summary` field at the end of the round, name each reference you opened and the specific recommendation from it that shaped your implementation.** If you skipped a reference because you already had recent context on it, say that — but you must say *which* reference and *why*.

If you cannot identify a relevant reference for a task, search the `references/` tree before falling back to priors. The cost of opening one wrong file is tiny; the cost of an unread one is a round of wasted implementation.
