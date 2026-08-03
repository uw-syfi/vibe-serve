Use the pre-staged model weights described by the runtime environment; do not
download model weights. Model weights are at `/model` in local environments;
remote environments may require mounting the declared model volume instead.

## Toolchains

For candidate components that use Python, use `uv` for package management and
execute their scripts through the workspace environment. This is a rule for
Python components, not a requirement that the serving hot path, scheduler,
transport, kernels, or build use Python. Use the selected language's native,
reproducible build tooling for non-Python components and integrate that build
with the declared startup and evaluation lifecycle.

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

Treat activation telemetry as part of the hot path. Before benchmarking, audit
every counter/gauge update added inside token, layer, or request loops and count
device-to-host synchronization sites such as `.item()`, `.tolist()`, CPU copies,
or explicit synchronizes. Maintain totals and high-water marks incrementally in
host state when possible; do not rescan device tensors or live requests on every
decode step merely to publish `/health`. If a gauge requires a synchronized
sample, collect it outside the measured path or at a bounded low frequency and
measure the observer overhead first.

Before a paid profiling launch, list the decisions the capture must support and
audit all production branches and plausible residuals that could change those
decisions. Add the complete set of non-overlapping scopes, counters, and
timestamps in one instrumentation pass, then exercise every required scope with
a local synthetic probe. Do not pay for a sequence of profiles that discovers
one missing scope at a time. Compare the captured row with the retained
uninstrumented operating point: if instrumentation materially changes useful
batch, cycle time, or throughput, preserve it only as qualitative diagnostic
evidence and do not derive an end-to-end Amdahl bound from its section totals.
Reject any generated `next_major` recommendation whose activation is already
positive and fallback-free in the same artifact.

Before changing streaming transport or chunking, read the trusted benchmark's
token-accounting code. If it treats each nonempty SSE record as one output token,
preserve exactly one model-delta record per generated model token. Retain a
targeted artifact comparing generated token IDs, nonempty model-delta records,
and reported completion tokens for each request. Coalescing several complete SSE
records into one transport write is allowed; splitting one model token across
records or merging multiple model tokens into one counted record is a metric
artifact, not a performance optimization.

## Use references as implementation support, not as a search policy

The `serving-systems` skill provides technical references. After the active
hypothesis identifies a concrete mechanism, open the router and the smallest
set of references that directly cover that mechanism before editing code. Do
not browse the library for an optimization to try merely because one is
available. In your summary, name the references used and the specific contract
or pitfall they clarified.
