# Profiling — contract

Profiling answers one question: *where does the time go?* The discipline is portable; the toolchain is entirely platform-specific, so the concrete tools live under `platforms/<backend>/profiler.md`.

## Altitudes

Every platform's profilers fall into three altitudes. Pick by what you already know, not by what's installed:

| Altitude | Answers | Use when |
|:--|:--|:--|
| **System timeline** | host/device overlap, gaps, launch and transfer cost | a new problem — this classifies it |
| **Framework / op** | which op or Python frame dominates | the timeline says host-bound |
| **Kernel-internal** | occupancy, memory throughput, stalls | the timeline says one kernel dominates |

**Always classify before descending.** Kernel-internal tools have high overhead and answer a question you may not have. If a system-level profile shows the accelerator idle 60% of the time, no amount of kernel tuning helps.

## Invariants

1. **Profile the steady state.** Skip warmup, compilation, cache population, and first-call allocation. A profile that includes them describes startup, not serving.
2. **Profile a realistic workload.** The bottleneck at batch 1 and batch 64 are different bottlenecks. Profile the shape you intend to serve.
3. **One variable at a time.** A profile taken after three simultaneous changes cannot attribute the delta.
4. **Export metrics, don't read screenshots.** Comparisons across runs need numbers.
5. **Benchmark hygiene first.** A profile of a badly-constructed benchmark faithfully describes the wrong thing. See [`tooling/serving-benchmark.md`](serving-benchmark.md).

## Classification → fix

The mapping from finding to remedy is portable even though the tools aren't:

| Finding | Likely fix |
|:--|:--|
| Gaps *between* steps | [`algorithms/async-scheduling.md`](../algorithms/async-scheduling.md) — host scheduler stall |
| Gaps *between* kernels | the backend's launch-overhead remedy (graph capture where it exists) |
| One kernel dominates | kernel-internal tools; consider a different kernel library first |
| Memory-bandwidth bound at decode | expected — check KV layout, quantization, batch size |
| Collective-bound | [`algorithms/parallelism.md`](../algorithms/parallelism.md) — topology and sharding |
| High device utilization but low throughput | utilization ≠ efficiency; descend to kernel altitude |

## Anti-patterns

- Descending to kernel altitude before a system-level diagnosis.
- "Increase batch size" without bottleneck evidence.
- Treating high utilization as proof of efficiency.
- Profiling a run that includes compilation or cache warmup.

## Platform toolchains

| Backend | System | Framework | Kernel |
|:--|:--|:--|:--|
| `cuda` | Nsight Systems (`nsys`) | torch profiler | Nsight Compute (`ncu`) |
| `rocm` | `rocprofv3` | torch profiler | `omniperf` |
| `trainium` | `neuron-explorer` | torch profiler | NKI profile tooling |
| `metal` | Instruments | MLX metal trace | Instruments GPU counters |
| `cpu` | `perf` / Instruments | torch profiler | `perf annotate` |

## See also

- [`tooling/serving-benchmark.md`](serving-benchmark.md) — the benchmark is the profiler's input; get it right first
- [`platforms/`](../platforms/) — the selected backend's `profiler.md`
