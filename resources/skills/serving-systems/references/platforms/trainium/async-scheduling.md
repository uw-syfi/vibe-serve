# Overlap scheduling on Trainium

Implements [`algorithms/async-scheduling.md`](../../algorithms/async-scheduling.md). The contract's problem — host scheduler work stalling the device — applies, but the graph is compiled ahead of time rather than launched kernel-by-kernel, so the mechanism is about **keeping the execution queue fed**, not about ordering streams.

## The model

`torch_neuronx` / NxD submit a compiled graph execution to the Neuron runtime, which queues and executes it. There is no per-kernel launch for the host to hide, and no stream/event API to orchestrate.

What remains is: while the device executes step N, the host must have step N+1's inputs ready to submit. If it doesn't, the queue drains and the device idles between executions.

## The lever: submit before you settle

```
# serialized — host reads step N's output before preparing N+1
out = model(step_input)
tokens = out.cpu()                   # blocks; queue drains
next_input = prepare(tokens)

# overlapped — keep the queue non-empty
out = model(step_input)              # submitted, executing
... host bookkeeping that does not depend on `out` ...
tokens = out.cpu()                   # settle only when needed
```

Because the KV cache is device-resident and aliased ([`nxd-kv-cache.md`](nxd-kv-cache.md)), most of step N+1's state does *not* depend on reading step N's output back to the host — the cache advanced in place. That is what makes overlap possible; a design that round-trips KV through the host has nothing to overlap.

## Satisfying the contract's invariants here

| Invariant | How |
|:--|:--|
| 1. Pipeline depth 2 | One execution in flight while the host prepares the next |
| 2. Future-typed values | The device tensor before `.cpu()`; on-device sampling keeps the sampled token from becoming a host dependency |
| 3. One ordered sync point | The single `.cpu()` per step that pulls emitted tokens for detokenization |
| 4. Serialize on demand | Any host-side decision (grammar, admission on a new bucket) forces a settle; the loop degrades to synchronous |

## The dominant stall is upstream of this

On Trainium the usual cause of device idle is **not** scheduler overhead. In order of frequency:

1. **KV crossing the graph boundary per token** — see [`floor.md`](floor.md) §3. Presents as host-bound; fix this before anything here.
2. **Host-side sampling** — a per-token round trip. Move sampling on device.
3. **A recompile mid-run** — an unbucketed shape reached the compiler. Fix the bucket ladder in [`continuous-batching.md`](continuous-batching.md).
4. *Then* scheduler-level overlap.

Applying this file's technique before fixing 1–3 optimizes a small term while a large one dominates. The profiler ([`profiler.md`](profiler.md)) distinguishes them: recompiles and boundary transfers are visible on the timeline; scheduler overhead is the residual gap once they are gone.

## What does not apply

| CUDA concern | Here |
|:--|:--|
| Stream creation and `wait_stream` | N/A — no stream API |
| CUDA events as memory barriers | N/A |
| Graph capture inside an event-ordered region | N/A — graphs are compiled, not captured |

## See also

- [`floor.md`](floor.md) — the higher-priority optimizations
- [`nxd-kv-cache.md`](nxd-kv-cache.md) — why overlap is possible at all
- [`neuron-pytorch.md`](neuron-pytorch.md) — host-sync pitfalls
- [`profiler.md`](profiler.md) — telling these stalls apart
