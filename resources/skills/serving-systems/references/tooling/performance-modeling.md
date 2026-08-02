# Serving Performance Modeling

Build a calibrated analytical model before choosing expensive serving-system
optimizations, and refresh it when progress plateaus.

## Prerequisites

- A faithful end-to-end benchmark result for the target workload.
- The model architecture, precision, hardware, and operator constraints.
- At least coarse timing or profiler evidence from the production serving path.
- Separate TTFT, TPOT, latency, throughput, failure, and offered-load data when
  the objective exposes them.

Do not treat an uncalibrated hardware peak or a profiler self-time summary as a
prediction of service performance.

## Model three different ceilings

| Ceiling | Question | Inputs |
|:--|:--|:--|
| Hardware/workload | What could this model and workload approach on this hardware under ideal execution? | FLOPs, bytes, usable compute and bandwidth, request shape |
| Current architecture | What could the present scheduler, batching, execution, and transport design approach after plausible optimization? | Measured phase times, occupancy, overlap, batching, residual time |
| Hypothesis | What is the maximum end-to-end gain from the proposed mechanism? | Cost-center share and the fraction the mechanism can remove |

Keep these ceilings separate. A mechanism can approach its own ceiling while
leaving the architecture far below the objective.

## Start from the target gap

For a throughput objective:

```text
required_speedup = target_throughput / current_throughput
```

For a latency objective, use the corresponding reduction factor and preserve
the other objective constraints. Compute the gap at the same workload shape and
operating point; do not mix concurrency, sequence lengths, precision, request
mode, or failure policy.

## Account for end-to-end time

Use non-overlapping phases where possible:

```text
T_client = T_queue + T_prefill + T_decode + T_host + T_transport + T_residual
```

If phases overlap, model the critical path rather than summing their durations.
Always retain:

```text
coverage = explained_nonoverlapping_time / client_observed_time
residual = client_observed_time - explained_nonoverlapping_time
```

A large residual is a profiling result: locate it before optimizing a fully
accounted minor phase. Correlate client, HTTP, scheduler, model-runner, and GPU
timestamps when the service boundary matters.

## Bound a hypothesis with Amdahl's law

If a fraction `f` of end-to-end time is accelerated by `s`:

```text
total_speedup = 1 / ((1 - f) + f / s)
perfect_removal_ceiling = 1 / (1 - f)
```

Use a range for `f` and `s`. Reject false precision. A small bound can still
justify a cheap experiment or prerequisite, but it is not a structural path to
a much larger target gap.

## Build the device roofline

For one execution step:

```text
arithmetic_intensity = useful_FLOPs / bytes_moved_from_HBM
ridge_point = usable_compute_FLOPs_per_s / usable_HBM_bytes_per_s
roofline_FLOPs_per_s = min(
    usable_compute_FLOPs_per_s,
    arithmetic_intensity * usable_HBM_bytes_per_s,
)

T_device_lower_bound = max(
    step_FLOPs / usable_compute_FLOPs_per_s,
    step_bytes / usable_HBM_bytes_per_s,
)
device_token_ceiling = useful_tokens_per_step / T_device_lower_bound
```

Use measured or defensibly discounted compute and bandwidth, not marketing
peaks. Include weight reads, activation traffic, KV-cache reads/writes,
attention metadata, padding, and communication that the execution actually
requires. Recompute for each relevant batch and context-length regime.

Dense autoregressive decode often raises weight reuse with batch size while KV
traffic grows with live context. Prefill and decode therefore need separate
models. Mixed prefill/decode scheduling needs a weighted or trace-derived model,
not a single generic tokens-per-step estimate.

## Connect device and service ceilings

For a decode step producing `B_useful` request tokens:

```text
T_cycle = critical_path(
    T_device,
    T_scheduler,
    T_sync,
    T_postprocess,
    T_transport,
)
throughput_ceiling = B_useful / T_cycle
```

Account for padding, inactive slots, graph buckets, admission limits, prefill
interruptions, failures, and queue residence. A kernel roofline does not include
these service losses.

Model latency separately:

```text
TTFT = queue + tokenize + prefill + first_decode + first_transport
TPOT = steady_decode_cycle / useful_decode_progress
E2E = TTFT + remaining_decode_and_drain
```

Throughput parity that violates TTFT, TPOT, latency, accuracy, precision, or
failure constraints is not parity.

## Calibrate with measurements

1. Predict at least one measured operating point before using the model for
   planning.
2. Compare predicted and observed step time, throughput, TTFT, and TPOT.
3. Record the residual and the assumptions most capable of explaining it.
4. Prefer a small discriminating profile or A/B probe when uncertainty changes
   the ranking of hypotheses.
5. After an experiment, update both the phase estimate and the end-to-end model.

Use multiple concurrency points when saturation behavior matters. A model that
fits only the selected best row cannot distinguish useful batching from overload
or queueing.

## Plateau workflow for the outer loop

Refresh the model after the first valid baseline, after a material architecture
change, and whenever the campaign plateaus.

1. Restore or identify the best trusted checkpoint.
2. Quantify the remaining target gap at comparable operating points.
3. Reconcile client-observed time with non-overlapping measured phases.
4. Recompute device, architecture, and per-hypothesis ceilings as ranges.
5. Name the assumptions and unexplained residual that dominate uncertainty.
6. Compare candidate mechanisms by plausible end-to-end headroom and experiment
   cost.
7. Propose either a structural hypothesis with sufficient headroom or the
   smallest measurement that could materially change the model.

Do not respond to a plateau with another local change in the same cost-center
class unless new evidence changes its bound.

## Recommended outer-loop record

```text
Model provenance:
- candidate commit / architecture fingerprint
- source benchmark and profiler artifacts
- measurement timestamp and workload identity

Current objective metrics:
Comparable reference metrics:
Required multiplicative improvement:

End-to-end accounting:
- cost center, measured range, overlap relationship, evidence artifact
- unexplained residual and coverage

Ceilings:
- hardware/workload range and assumptions
- current-architecture range and assumptions
- each candidate hypothesis's Amdahl bound

Model check:
- predicted versus measured result at representative operating points
- dominant uncertainty

Decision:
- selected hypothesis or discriminating measurement
- expected end-to-end range
- observation that would invalidate the model
```

## Freshness and calibration gate

A copied model is not a refreshed model. After a scheduler, cache layout,
kernel backend, precision, graphing strategy, or transport path changes, verify
that every architecture-dependent statement still describes the production
path. Record a candidate commit or equivalent architecture fingerprint and the
exact artifacts used for calibration.

Before using the model to rank another optimization, require all of:

1. Numerical FLOPs and byte assumptions for the relevant prefill/decode regime.
2. Usable device compute and bandwidth ranges with their source or discount.
3. A hardware/workload ceiling distinct from the current implementation knee.
4. A prediction for at least one retained operating point, its observed value,
   calibration error, and the residual the model does not explain.
5. TTFT, TPOT, end-to-end latency, failures, and accuracy treated as constraints
   rather than inferred from throughput.

Reject the model as stale when it names a removed bottleneck, contradicts
activation telemetry, cannot reproduce any retained measurement, or reports a
measured saturation point as though it were the hardware roofline.

## Pitfalls

- Mixing per-kernel, per-step, and client-observed metrics.
- Summing profiler categories that overlap or contain parent/child scopes.
- Treating CUDA-graph attribution gaps as zero work.
- Using peak hardware specifications without an efficiency range.
- Ignoring host, scheduler, transport, or queueing time.
- Modeling successful tokens while silently discarding failed requests.
- Changing precision, workload, or semantics outside operator constraints.
- Optimizing a measured fraction whose perfect-removal ceiling cannot explain a
  material part of the remaining gap.

## See also

- [`../../OVERVIEW.md`](../../OVERVIEW.md) — roofline and prefill/decode foundations.
- [`profiler.md`](profiler.md) — collect system timelines and kernel roofline evidence.
- [`serving-benchmark.md`](serving-benchmark.md) — measure comparable end-to-end serving metrics.
- [`../hardware/nvidia.md`](../hardware/nvidia.md) — hardware specifications for NVIDIA targets.
