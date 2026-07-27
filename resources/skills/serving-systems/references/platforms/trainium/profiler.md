# Profiling on Trainium

`neuron-explorer` is the system-timeline tool. It replaces `nsys` in the workflow described by [`tooling/profiler.md`](../../tooling/profiler.md) — read that first for the altitude discipline; this file covers only what is Neuron-specific.

## Capture

Profiling is driven by the Neuron runtime rather than by wrapping the process. Enable capture via the runtime's profile environment/config, run a steady-state workload, then open the produced artifact in `neuron-explorer`.

The invariant that matters most here: **exclude compilation.** A Trainium profile that includes `neuronx-cc` invocations describes the compiler, not the model. Warm the compile cache, run enough steps to reach steady state, then capture.

## What the timeline shows

| Signal | Meaning |
|:--|:--|
| Long gaps between executions | host-bound — the runtime queue is starving; check for per-token host sync |
| Recompilation events mid-run | an unbucketed shape reached the compiler (lazy-tensor path only — a traced deployment raises instead); fix the bucket ladder |
| KV transfer at the graph boundary | the cache is crossing the device edge per token — see [`nxd-kv-cache.md`](nxd-kv-cache.md) |
| High SBUF/PSUM pressure | activation peak; consider the NKI flash kernel ([`neuron-flash-attention.md`](neuron-flash-attention.md)) |

## Neuron-specific classification

The generic finding→fix table in the contract mostly transfers, with two substitutions:

- "Gaps between kernels → graph capture" becomes **"gaps between executions → check bucketing and queue depth."** There is no capture step to add; the graph is already compiled.
- "One kernel dominates → kernel-internal tools" routes to the `neuron-nki-profiling` and `neuron-nki-profile-querying` skills, which cover NeuronCore kernel-level analysis.

## The characteristic Trainium finding

A decode that looks uniformly slow, with no single dominant operator, is usually the KV cache crossing the graph boundary every token rather than living in resident aliased buffers. It presents as host-bound rather than as a slow kernel, which is why the system timeline is the right altitude to catch it.

## See also

- [`floor.md`](floor.md) — what the profile should be pushing you toward
- [`neuron-pytorch.md`](neuron-pytorch.md) — host-sync pitfalls
- `neuron-nki-profiling`, `neuron-nki-profile-querying` — kernel altitude
