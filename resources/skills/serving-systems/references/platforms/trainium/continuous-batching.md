# Continuous batching on Trainium

Implements [`algorithms/continuous-batching.md`](../../algorithms/continuous-batching.md). Read the contract first — the five invariants hold here unchanged. What differs is the KV strategy, and it differs *by inversion*: on CUDA the goal is to eliminate padding; here padding to a bucket is the correct design.

## Why the CUDA design doesn't transfer

`neuronx-cc` compiles a graph for a specific shape. A scheduler that varies batch size or sequence extent per step presents a new shape each time, and each new shape is a compile — seconds to minutes, on the serving hot path.

Variable-length packing and paged KV, the two CUDA answers, both exist specifically to make shapes dynamic. Porting either here maximizes the thing that hurts most.

## The design: static buffers, bucketed shapes

```
fixed batch slots B  ×  bucketed sequence extent L
      ↓
one compiled graph per (B, L) bucket pair
      ↓
admission fills slots; padding fills the remainder
```

- **Fixed slot count.** Pre-allocate `B` request slots. The batch tensor is always `B` wide; inactive slots are masked, not removed. Contract invariant 4 (independent finish) is satisfied by masking a slot, not by resizing the batch.
- **Bucketed extent.** Round each request's KV extent up to the next bucket. Recompile happens once per bucket, at warmup, not per request.
- **Padding is permanent.** It is not a stage to grow out of. The waste is real and is the price of static shapes.

## Bucket ladder

The ladder is the main tuning knob:

| Ladder | Effect |
|:--|:--|
| Few, wide buckets | Less compilation, more padded waste per request |
| Many, narrow buckets | Tighter fit, more compiled artifacts and longer warmup |

Start with powers of two over the expected prompt distribution (e.g. 512 / 1024 / 2048 / 4096) and one or two decode-extent buckets. Every bucket must be compiled and cached at warmup — a bucket first reached in production is a stall.

Combine with a persistent compile cache ([`floor.md`](floor.md) §2) so the ladder is paid for once across process restarts, not once per start.

## KV cache

Use the device-resident, in-place cache — [`nxd-kv-cache.md`](nxd-kv-cache.md). This is the decisive decode optimization and it composes directly with the static-slot design: the resident buffers are sized `B × L_bucket` and aliased across steps, so admission and eviction are slot bookkeeping rather than allocation.

Do **not** build a block pool and page table. There is no fragmentation problem to solve — the buffers are statically sized — and the indirection costs without paying.

## Satisfying the contract's invariants here

| Invariant | How |
|:--|:--|
| 1. Per-request position | Position tensor is `B`-wide; each slot carries its own extent |
| 2. Per-request masking | Mask covers both padding-to-bucket and inactive slots |
| 3. Admission between steps | Fill free slots before the step; never resize mid-step |
| 4. Independent finish | Mark the slot free and mask it; batch width is unchanged |
| 5. No per-request host sync | On-device sampling — see [`floor.md`](floor.md) §5 |

## Pitfalls

- **A bucket reached first in production.** Compile stall mid-serving. Warm every bucket at startup.
- **Slot count as a tuning dial at runtime.** Changing `B` is a new shape. Fix it at compile time.
- **Host-side sampling.** A per-token device→host round trip dominates step time here; it undoes the batching win.
- **Assuming padded slots are free.** They consume the same compute as real ones. Utilization, not just throughput, should drive the slot count.
- **Copying a CUDA scheduler wholesale.** The tell is a KV allocator with a free list — that structure has no purpose on this backend.

## See also

- [`floor.md`](floor.md) — static shapes, compile cache, resident KV
- [`nxd-kv-cache.md`](nxd-kv-cache.md) — the resident aliased buffer model
- [`neuron-pytorch.md`](neuron-pytorch.md) — bucketing mechanics and host-sync pitfalls
