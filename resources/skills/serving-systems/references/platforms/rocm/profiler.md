# Profiling on ROCm

The workflow mirrors CUDA's — classify with a system timeline, then descend — so read [`tooling/profiler.md`](../../tooling/profiler.md) for the discipline and treat this as the tool substitution table.

> **Status: experimental.** Not exercised end to end against MI300-class hardware in this repo. Verify flags against your ROCm version.

## Tool substitutions

| Altitude | CUDA | ROCm |
|:--|:--|:--|
| System timeline | `nsys` | `rocprofv3` |
| Framework / op | torch profiler | torch profiler (works unmodified on ROCm) |
| Kernel-internal | `ncu` | `omniperf` |

`torch.profiler` is the reason vibesys can use `ProfilerKind.TORCH` on this backend without a dedicated profiler kind — it works on ROCm as-is and covers the framework altitude.

## Notes

- **Start with `rocprofv3`** for a new problem, exactly as you would start with `nsys`. The classification it produces — host-bound, launch-bound, memory-bound, collective-bound — maps onto the same finding→fix table in the contract.
- **`omniperf` has the same overhead warning as `ncu`.** Target specific kernels; do not profile a whole run at kernel altitude.
- **Collectives** show as RCCL rather than NCCL. The topology reasoning differs — Infinity Fabric, not NVLink — see [`floor.md`](floor.md) and [`algorithms/parallelism.md`](../../algorithms/parallelism.md).

## The characteristic ROCm finding

A kernel that is present and correct but markedly slower than its NVIDIA counterpart usually means a fallback path was taken — the specific attention variant or quantization scheme isn't covered by AITER/CK on this ROCm version and silently landed on Triton or SDPA. Confirm which kernel actually ran before concluding the hardware is the limit.

## See also

- [`floor.md`](floor.md) — the optimization floor
- [`aiter.md`](aiter.md) — kernel coverage, which determines whether a fallback was taken
- [`hardware.md`](hardware.md) — bandwidth and precision by SKU
