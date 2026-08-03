## Evidence-led optimization method

Establish the smallest faithful runnable baseline, then choose work from
measured end-to-end evidence—not a catalog of popular techniques. Every
hypothesis must name:

1. the measured critical-path cost it removes and its maximum end-to-end gain;
2. the smallest causally complete production-path change, including coordinated
   multi-component replacement when micro-edits cannot activate it;
3. production-path activation evidence and falsification criteria; and
4. the workload, fidelity, and operator invariants it preserves.

Quantify the multiplicative gap to a declared reference. While it exceeds 2x,
do not spend a canonical round on a mechanism bounded to single-digit gain
unless it is necessary correctness/measurement work. Prefer a bottleneck class
with at least 20% defensible headroom and show the arithmetic. Carry a required
structural replacement as one persistent hypothesis with bounded end-to-end
slices even if it changes toolchain, process boundary, or most candidate code.

Use headroom to rank hypotheses, not to grade implementations. Give each plan a
forecast `expected_effect` range and a separate `minimum_acceptance_criteria`
derived from noise, binding constraints, complexity, resource cost, and
composability. A smaller material Pareto gain may clear that minimum; retain it
and calibrate the optimistic forecast.

For each shortlist item state the current metric, defensible gain range, implied
post-change metric range, remaining target gap, and whether it is terminally
sufficient on its own. Otherwise select it only as a necessary prerequisite or
cheap bounded discriminator, and name the later structural requirement. Rank by
target-relevant headroom and information gained per accelerator minute, not
implementation ease.

For multi-objective work, terminal sufficiency requires one jointly attainable
operating point meeting every throughput, TTFT, TPOT, latency, failure, and
other gate. An optimistic latency bound still misses even when throughput
crosses parity; do not combine unrelated optimistic range endpoints. Label any
remaining miss and the required composable mechanism.

At the first baseline, after material architecture change, or on a plateau,
read `skills/serving-systems/references/tooling/performance-modeling.md` and
refresh the analytical model. Reconcile client time with non-overlapping costs
and residual, distinguish hardware/workload from current-architecture ceiling,
and compute a ranged Amdahl/roofline bound with assumptions. If uncertainty
changes the ranking, request the smallest discriminating profile.

Before a paid profile, write a decision-oriented coverage plan: list every
ranking-relevant residual and active branch, the non-overlapping scopes/counters/
timestamps that distinguish them, local synthetic activation, and an
observer-effect control. Do not discover one missing scope per cold launch.
Materially perturbed captures are qualitative, not quantitative Amdahl evidence;
never recommend a mechanism that the same capture shows fully active.

Do not call the reference engine's observed score a hardware ceiling. Compute
an independent FLOP/byte bound and use the reference only as an achievability
check; null/unparseable evidence is missing. Before blaming host/device overlap,
inventory hot-path synchronization frequencies. Discard phase attribution that
synchronizes at every scope boundary or adds overlapping CPU and CUDA durations.

Build the roofline for the whole model decode step: all decode-touched weight
bytes from dimensions/precision, dense projection/MLP/output FLOPs, KV reads and
writes, and useful tokens per batch. Attention-only analysis is a kernel
ceiling. Use attainable compute/bandwidth ranges or label hardware peak as an
optimistic upper bound.

Convert terminal throughput into step feasibility: calculate required cycle
time at current useful-token capacity and minimum useful tokens per step at the
credible cycle floor. Compare measured active batch and admission, graph-bucket,
KV, and memory limits. If existing-step timing cannot reach the target with
service margin, rank a capacity or multi-token experiment alongside kernels.
Preserve latency gates: queued concurrency is not useful batch work. Keep the
capacity gap explicit and retain a named roadmap item for its concrete limiter
until measured.
A prerequisite may precede it only for terminal latency or one bounded
discriminator; quantify the reason, bound the detour to one decision, and name
the capacity experiment that follows.

Require operator-level before/after evidence for layout, fusion, and kernel
claims: the removed operation, frequency, bytes/launches, source path, and
runtime activation. A paged-KV claim whose path gathers/indexes logical KV
before dense attention is an allocator/layout experiment, not paged-attention
compute.

Put an observer-overhead invariant in telemetry plans: inventory `.item()`,
`.tolist()`, CPU copies, and synchronization frequency inside token/layer/request
loops; maintain totals/peaks incrementally instead of rescanning live device
state. Telemetry cannot fairly falsify its mechanism if it adds comparable sync.

Treat streaming record granularity as measurement contract. Inspect how the
client derives token count, TTFT, and TPOT. If nonempty SSE records are tokens,
preserve one record per generated model token and compare token IDs, delta
records, and completion counts. Multiple complete records may share a write;
splitting/merging their model-token accounting is invalid.

## Scoping and performance criteria

- For endpoint/schema work, name the exact surface and authoritative contract;
  grow it only as required.
- Judge performance on the end-to-end headline metric. Per-call timing is causal
  evidence because fewer slower calls can still win. Avoid startup-only gates.
- Scope static inspection to precise implementer-owned files, excluding
  framework evaluator/profiler sources.
- Do not choose a technique merely because another serving system uses it;
  consult technical references only after evidence identifies the mechanism.
