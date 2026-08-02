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

Do not choose a technique merely because other serving systems commonly use it.
Consult a technical reference only after the evidence identifies the mechanism
you need to understand.

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
