Use the pre-staged model weights described by the runtime environment; do not
download model weights. Model weights are at `/model` in local environments;
remote environments may require mounting the declared model volume instead.

## Python toolchain

Use `uv` for Python package management. Run `uv init --no-vcs` if `pyproject.toml`
doesn't exist yet, and `uv add` for new dependencies. Always execute Python
scripts via `uv run`.

The independent judge and framework-owned gates apply in addition to this
round's pass criteria. Your implementation must preserve those contracts.

When changing batching, request-slot reuse, KV-cache layout, attention masks,
or scheduling, run a targeted concurrent mixed-length correctness probe before
using performance evidence. Compare deterministic outputs against the trusted
unbatched or reference path for prompts of different token lengths, including
at least one request that finishes while others remain active. A single-request
accuracy pass is not evidence that cache rows, positions, or masks stay aligned
across a dynamic batch. Retain the probe inputs, outputs, and comparison result
so the judge can audit the invariant without repeating an expensive run.

For a structural layout, fusion, or kernel hypothesis, trace the production
request path to the actual attention/operator call before launching a target
accelerator benchmark. Record the old and new hot-path operations and the
frequency, bytes, or launches the change is meant to remove. A new class,
backend flag, cache layout, or activation counter is not sufficient when the
same expensive operation remains underneath it. In particular, do not call a
KV path paged attention when it materializes the logical sequence with indexing
or a gather before dense attention; the attention kernel itself must consume
the page table. If static inspection shows that the claimed operation was not
removed, fix the production path or report the hypothesis as not fairly tested
without spending on the representative benchmark.

## Use references as implementation support, not as a search policy

The `serving-systems` skill provides technical references. After the active
hypothesis identifies a concrete mechanism, open the router and the smallest
set of references that directly cover that mechanism before editing code. Do
not browse the library for an optimization to try merely because one is
available. In your summary, name the references used and the specific contract
or pitfall they clarified.
