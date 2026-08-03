Use the pre-staged model weights from the runtime; never redownload them. Local
weights are at `/model`; remote runs mount the declared model volume.

For candidate components that use Python, use `uv` and the workspace
environment. The serving hot path, scheduler, transport, kernels, and build need
not remain Python; use reproducible native tooling integrated with the declared
startup/evaluation lifecycle. Independent judge and framework gates remain
binding.

For batching, slot reuse, KV layout, masks, or scheduling, run a targeted
concurrent mixed-length correctness probe before accepting performance. Compare
deterministic production-path outputs with trusted unbatched/reference results,
including a request that finishes while others remain active. Retain inputs,
outputs, and comparison; single-request accuracy cannot prove cache/mask/position
alignment.

For layout, fusion, or kernel work, trace the production path to the actual
operator before paid hardware. Record old/new operations and removed frequency,
bytes, or launches. A class, flag, layout, or counter is not activation if the
same expensive operation remains. A KV path that gathers/indexes logical pages
before dense attention is not paged attention: the attention kernel itself must
consume the page table. Fix or report this before representative benchmarking.

Treat activation telemetry as part of the hot path. Inventory every counter and
`.item()`, `.tolist()`, CPU copy, or synchronization inside token/layer/request
loops with its frequency. Maintain host totals/high-water marks incrementally;
sample synchronized gauges outside measurement or at bounded frequency and
measure observer overhead.

Before paid profiling, write the decisions and plausible residuals it must
resolve. Instrument all non-overlapping scopes/counters/timestamps in one pass,
then exercise every scope locally; do not discover one missing scope at a time.
Compare useful batch, cycle, and throughput with the retained control. A
materially perturbed capture is qualitative only, not an end-to-end Amdahl
bound. Reject `next_major` when its mechanism is already positive and
fallback-free in that artifact.

Before streaming/chunking work, inspect benchmark token accounting. When each
nonempty SSE record counts as one output token, preserve exactly one model-delta
record per generated model token. Retain per-request token IDs, nonempty records,
and completion counts. Several complete records may share a transport write;
splitting or merging model-token accounting is a metric artifact.

## Use references as implementation support

Once evidence and the active hypothesis identify a mechanism, open the
`serving-systems` skill router and only its directly relevant references before
editing. Do not browse it for arbitrary ideas. Name each reference used and the
contract or pitfall it clarified.
