## Evidence-led optimization method

First establish the smallest faithful, runnable serving baseline required by
the input contract. After that, choose work from measured end-to-end evidence
rather than from a memorized list of popular techniques.

For every proposed hypothesis:

1. Identify the measured workload phase or critical-path cost it removes.
2. Bound the maximum possible end-to-end gain from that cost before investing.
3. Prefer the smallest change that tests the causal mechanism cleanly.
4. Require observable activation evidence from the production serving path.
5. State what result would falsify the hypothesis and preserve all workload,
   model-fidelity, and operator constraints.

When the objective names a measured reference target, quantify the remaining
multiplicative gap before choosing the next round. If the candidate is still
more than 2x from that target, do not spend a canonical round on a mechanism
whose own measured cost or defensible upper bound can only yield a single-digit
percentage improvement, unless it is necessary correctness or measurement
work. Choose a bottleneck class with enough headroom to remove a material part
of the gap (as a default, at least 20%) and put the arithmetic in `reasoning`.
Small tuning remains appropriate as a targeted probe, but repeated local
frontier nudges are not a substitute for a structural path to the target.

Use the headroom calculation to rank hypotheses, not to grade implementations.
For each plan, write an `expected_effect` range and a separate
`minimum_acceptance_criteria`. Derive the latter from end-to-end benchmark
noise, binding latency/accuracy constraints, implementation complexity,
resource cost, and whether the change composes with the remaining path. Do not
copy the predicted midpoint or optimistic ceiling into the acceptance gate. A
material Pareto improvement below forecast is evidence that the model was
optimistic; retain the improvement when it clears the independent minimum and
recalibrate the model.

Make that bound decision-auditable in absolute as well as relative terms. For
each shortlisted mechanism, state the current measured headline metric, the
defensible end-to-end improvement range, the implied post-change metric range,
and the gap that would still remain to the terminal target. Explicitly label
whether the mechanism is terminally sufficient on its own. If even its
optimistic bound cannot reach the target, select it only when it is a necessary
critical-path prerequisite or a cheap discriminating experiment; name that
reason, bound the experiment before expensive evaluation, and identify the
later structural mechanism that would still be required. Rank plausible
alternatives by target-relevant headroom and information gained per
accelerator minute rather than by ease of implementation alone.

At the first valid baseline, after a material architecture change, and whenever
the framework reports a plateau, open
`skills/serving-systems/references/tooling/performance-modeling.md` and refresh
the analytical performance model before proposing another optimization. In
`reasoning`, reconcile client-observed time with non-overlapping measured cost
centers, report the unexplained residual, distinguish the hardware/workload
ceiling from the current-architecture ceiling, and calculate an Amdahl or
roofline-based end-to-end bound for the proposed mechanism. Use ranges and name
the assumptions. If uncertainty changes which hypothesis has the most
headroom, request the smallest discriminating profile instead of guessing.

Do not relabel a reference engine's observed score as an analytical hardware
ceiling. Compute the independent FLOP/byte bound and then use the reference as
an empirical achievability check. Treat `None`, `null`, or an unparseable value
in a cited experiment as missing evidence, not as a bound. Before classifying a
plateau as generic host/device overlap, inventory synchronization sites on the
token path with their per-step, per-layer, and per-request frequencies. Discount
or discard phase attribution from instrumentation that synchronizes at each
scope boundary, and never claim complete coverage by adding overlapping CPU and
CUDA-event durations.

Build the hardware/workload roofline for the whole model decode step. Reconcile
the architecture's parameter dimensions and precision into bytes for every
weight touched by decode, then include dense projection/MLP/output FLOPs, KV
reads and writes, and useful tokens per batch. A roofline that counts only the
attention kernel is a kernel ceiling, not a serving ceiling. Use an attainable
compute/bandwidth range; if only hardware peak is available, label the result an
optimistic upper bound and do not treat the gap to it as removable application
overhead.

Turn the terminal throughput target into a step-capacity feasibility check
before ranking execution-kernel work. Calculate both the required cycle time at
the current useful-token capacity and the minimum useful tokens per step at the
credible cycle-time floor. Compare those values with the measured active batch,
admission limit, graph-bucket limit, and memory-feasible capacity. If the target
would require a cycle below the device lower bound or leave no credible margin
for service overhead, do not assume optimizing the existing step can reach it:
rank the smallest capacity or multi-token capability experiment alongside the
kernel hypotheses. Preserve TTFT, TPOT, and latency constraints when testing a
larger useful-token capacity; queued concurrency is not useful batch work.

Do not choose a technique merely because other serving systems commonly use it.
Consult a technical reference only after the evidence identifies the mechanism
you need to understand.

For a structural layout, fusion, or kernel hypothesis, require an operator-level
before/after claim rather than accepting the name of a new class, flag, or data
structure as activation. Name the hot-path operation that disappears or becomes
cheaper, its frequency, and the bytes or launches affected. For example, a paged
KV attention claim requires the production attention kernel to consume the page
table directly; a path that first materializes the logical KV sequence with
indexing or a gather is still dense attention compute and should be classified
as an allocator/layout experiment. Require source inspection and runtime
telemetry for the actual request path before spending on a representative
benchmark.

Put an observer-overhead invariant in plans that add activation telemetry.
Require a source-level inventory of `.item()`, `.tolist()`, CPU-copy, and
synchronization sites introduced inside token, layer, and request loops. Totals
and peaks should be maintained incrementally instead of rescanning device state
or every live request each decode step. A mechanism cannot be fairly falsified
by a benchmark whose new telemetry adds synchronization at the same frequency
as the operation being optimized.

## Scoping API work

When a task touches an endpoint or message schema, name the exact surface being
changed and point the implementer to the authoritative contract reference.
Grow the API only as required by the objective and evaluator.

## Performance criteria

An implementation can make an individual operation slower while reducing how
often it runs. Per-call timing alone therefore cannot establish an end-to-end
win. Phrase performance gates on the objective's headline metric and use
lower-level measurements only as causal evidence. Avoid startup-only fixed
timing thresholds that do not capture the full request path.

For static-inspection criteria, name the implementer-owned file and prohibited
behavior precisely. Avoid repository-wide clauses that also match
framework-provided evaluator or profiler directories.
