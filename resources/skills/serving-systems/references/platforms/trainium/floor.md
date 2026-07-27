# Trainium (Neuron) optimization floor

Trainium is not a GPU. The optimization floor is genuinely different, and several CUDA reflexes are actively counterproductive here. Start from this file, not from the CUDA one.

## 1. Static-shape graphs

`neuronx-cc` compiles for a fixed shape. Any new shape triggers a recompile — seconds to minutes — so a dynamically-shaped serving loop that would be optimal on CUDA is pathological here.

- **Bucket** prompt and decode lengths; pad up to the nearest bucket.
- **Padding is correct on this backend**, not a baseline to eliminate. The CUDA guidance to remove padding via variable-length packing does not transfer.
- Keep the bucket ladder small — each bucket is a separate compiled artifact.

## 2. Persistent compile cache

Without a warm cache, every process start pays full compilation. Set the cache directory and persist it across runs and containers. A "slow startup" report on Trainium is almost always a cold compile cache.

## 3. Device-resident, in-place KV cache

**The decisive decode optimization.** Use NxD's `KVCacheManager` with `ModelBuilder` input/output aliasing so KV lives in resident `nn.Parameter` buffers.

- [`nxd-kv-cache.md`](nxd-kv-cache.md)

A from-scratch `torch_neuronx.trace` decode that passes the KV cache through the graph boundary every token is host-bound and slow — the cache crosses the device edge twice per token. This is the single most common Trainium serving mistake.

Note this is *not* paged attention. There is no block pool and no page table; the CUDA fragmentation problem it solves doesn't exist in the same form here.

## 4. Flash attention via NKI

Flash attention is **not** NVIDIA-only. The `FlashAttention` and `FlashInfer` libraries are, but the algorithm ships on Neuron as the NKI kernel `nki_flash_attn_func`.

- [`neuron-flash-attention.md`](neuron-flash-attention.md)

Use it instead of naive `softmax(QKᵀ)V` — it cuts the attention activation peak that drives HBM OOM and caps batch size.

## 5. BF16 and on-device sampling

BF16 is the default serving precision. Keep sampling on device — a host round-trip per token dominates step time at Trainium's decode latency.

## Then

- **Continuous batching over static buffers** — contract at [`algorithms/continuous-batching.md`](../../algorithms/continuous-batching.md), implementation at [`continuous-batching.md`](continuous-batching.md).
- **Custom NeuronCore kernels** — the bundled `neuron-nki-*` skills (`neuron-nki-writing`, `-docs`, `-debugging`, `-profiling`, `-profile-querying`). These are the Trainium analog of writing Triton/CUTLASS kernels, and unlike NVIDIA kernel authoring they are *in scope* for this collection.

## What does not apply

| CUDA technique | On Trainium |
|:--|:--|
| CUDA graphs | N/A — graphs are compiled ahead of time, not captured |
| Paged attention / block pool | N/A — see `nxd-kv-cache.md` for the resident-buffer model instead |
| Eliminating padding | **Inverted** — bucketed padding is required |
| FlashInfer / FlashAttention libraries | N/A — use the NKI flash kernel |

## See also

- [`hardware.md`](hardware.md) — NeuronCore-v3, SBUF/PSUM/HBM hierarchy, LNC config
- [`neuron-pytorch.md`](neuron-pytorch.md) — torch-neuronx, compilation, host-sync pitfalls
- [`nxd-inference.md`](nxd-inference.md) — turnkey path and building blocks
- [`profiler.md`](profiler.md) — neuron-explorer
