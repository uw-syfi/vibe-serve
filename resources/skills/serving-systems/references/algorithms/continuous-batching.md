# Continuous batching — contract

New requests join a running generation loop without waiting for in-flight requests to finish. Every serving backend needs this; the *mechanism* differs enough per platform that the implementation lives under `platforms/`.

This file is the portable part: what any implementation must guarantee, and how to tell when it is wrong.

## The problem

A single-request loop serializes: request B waits for request A to emit its final token. At any realistic arrival rate the accelerator idles between requests while the scheduler waits on one sequence. Continuous batching decouples admission from completion — the batch composition changes between decode steps.

## Invariants

Any correct implementation, on any backend, must hold all five:

1. **Per-request position.** Each sequence's next token is at its *own* KV length, not the batch's. RoPE encodes absolute position; sharing one `past_len` across a mixed batch silently corrupts every request but the longest.
2. **Per-request masking.** Requests in a batch have different valid KV extents. Positions outside a request's own extent must contribute exactly zero to its attention output.
3. **Admission between steps, never mid-step.** The active set is read once per forward. Mutating it during a step races the scheduler against the accelerator.
4. **Independent finish.** A request that hits EOS or `max_tokens` leaves the batch without disturbing the others' KV state or positions.
5. **No per-request host sync on the hot path.** One synchronization per step, not one per request. This is where naive implementations lose most of their throughput.

## Failure modes if skipped

| Symptom | Usually means |
|:--|:--|
| Throughput flat as concurrency rises | requests are serialized; admission isn't between-step |
| Output degrades only for short requests in a mixed batch | invariant 1 or 2 — position/mask derived from the batch, not the request |
| Per-step time scales with batch size, not with the largest member | per-request host sync (invariant 5) |
| Correct at batch 1, wrong at batch > 1 | masking of unused KV extent (invariant 2) |

## Platform implementations

The KV-storage strategy is the part that diverges, because it follows the backend's memory and shape model:

| Backend | Strategy | Why |
|:--|:--|:--|
| `cuda` | Eliminate padding — variable-length packing or paged KV | Dynamic shapes are free; padding wastes HBM and FLOPs |
| `rocm` | As cuda | Same dynamic-shape model |
| `trainium` | **Bucketed static shapes** — padding is required, not a flaw | `neuronx-cc` recompiles per shape; a dynamic batch triggers a recompile storm |
| `metal` | Unified memory, no separate device pool | Nothing to page; pressure is system-wide, not device-local |

**Read your backend's file before implementing.** The strategies are not variations on one design — on `cuda` the goal is removing padding, and on `trainium` padding to a bucket is the correct answer. Applying the CUDA arc on Trainium produces a scheduler that fights the compiler.

## See also

- [`algorithms/paged-attention.md`](paged-attention.md) — KV storage substrate (where the backend has a discrete pool)
- [`algorithms/chunked-prefill.md`](chunked-prefill.md) — interleave long-prompt prefill with active decodes
- [`algorithms/batched-sampling.md`](batched-sampling.md) — how invariant 5 is satisfied on the sampling side
- [`platforms/`](../platforms/) — the implementation for the selected backend
