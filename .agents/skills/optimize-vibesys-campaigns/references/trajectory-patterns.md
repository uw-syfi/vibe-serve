# Trajectory Patterns

Use this reference to turn campaign history into better decisions and reusable infrastructure.

## Patterns That Produced Progress

### Measure the full system boundary

Large gains often came from removing orchestration, queueing, serialization, or server-boundary overhead rather than tuning the model kernel. Compare concurrency scaling, CPU utilization, queue delay, device occupancy, and time-to-first-token to locate the boundary.

Lesson: profile and model end-to-end service time before assuming the GPU kernel is dominant.

### Preserve exploratory autonomy

The fastest useful iterations let the implementer choose narrow load sweeps, parameter changes, and diagnostics while the framework retained measurement authority. A fixed domain-specific evaluation API would have hidden bugs and constrained investigation.

Lesson: expose general execution and observation capabilities; standardize evidence, not every experiment.

### Retain valid tradeoffs

Some candidates delivered large throughput improvements with latency regressions. Treating only one scalar as success would discard useful architectural branches.

Lesson: store complete candidate vectors and use Pareto-aware selection under explicit hard constraints.

### Keep sessions warm, knowledge durable

A persistent implementer session reduced rediscovery of experiment quirks. Reusable scripts, runtime manuals, and skills made that knowledge survive session renewal.

Lesson: use session continuity for working memory and repository artifacts for institutional memory.

### Separate focused iteration from official measurement

Reusing a warm target for focused sweeps reduced repeated cold-start cost. Periodic official evaluation maintained accuracy and benchmark integrity.

Lesson: pay for comprehensive evidence at decision boundaries, not after every edit.

## Detours and Failure Modes

### Kernel work without verified activation

Adding or configuring an optimized kernel did not establish that production requests used it or that it addressed the dominant bottleneck.

Correction: require path activation evidence and compare predicted savings with end-to-end deltas before ruling a technique in or out.

### Repeated environment reconstruction

Agents rebuilt large local environments because source restoration and runtime cache ownership were entangled.

Correction: make the framework preserve fingerprinted environments and caches outside the restored candidate tree.

### Oversized role prompts

Planner and implementer prompts accumulated roadmap history, stable policy, evaluator details, and repeated instructions. This increased latency and diluted the current decision.

Correction: keep stable workflow in skills/runtime manuals, history in files, and continuation prompts delta-only.

### Duplicate evaluation

Implementer, judge, and framework sometimes repeated similar benchmark work because evidence authority was unclear.

Correction: let implementers run focused tests, judges audit immutable evidence, and the framework alone publish official results.

### Agent-authored restoration and packaging

Agents spent turns reconstructing snapshots, validation bundles, and fixtures. Failures in this shell work obscured the actual optimization.

Correction: move deterministic materialization, restoration, evidence packaging, and replay into framework operations.

### Local tuning after falsification

Multiple rounds adjusted parameters after evidence showed that the path was inactive or its maximum contribution was too small.

Correction: bound continuation leases and force a new designer bottleneck model when tuning falls inside noise.

### Infrastructure failures labeled as performance failures

Deployment, health, or evaluator failures sometimes caused useful candidates to be rejected or measurements to be interpreted as regressions.

Correction: classify failure phase explicitly and retry only when the candidate identity and remaining budget are valid.

### Over-specialized prompts

Naming a current file, language, server stack, provider, or favored technique anchored agents to incremental changes.

Correction: state interface and correctness constraints, invite implementation-substrate changes, and move environment specifics to capability adapters.

## Audit a Recent Window

For the last N rounds, build a compact table with:

| Round | Hypothesis | Change | Evidence type | Metric delta | Validity | Time/cost | Disposition | Information gained |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

Then answer:

1. How many rounds produced valid new information, even if performance regressed?
2. How many repeated a known failure or tuned within noise?
3. Where was time spent: inference, build, startup, evaluation, review, or recovery?
4. Did the agent target the measured gap or an assumed bottleneck?
5. Did official checkpoints arrive often enough to prevent drift?
6. Were good tradeoffs retained, and invalid results excluded?
7. Which repeated manual action should become framework behavior?
8. Is the next hypothesis a genuine pivot or another local variation?

Distinguish frontier efficiency from learning efficiency. A round can fail to improve the score yet be valuable if it decisively falsifies a plausible path. Repeated inconclusive rounds are the stronger sign of a broken loop.

## Convert Lessons Into Policy Carefully

Promote a lesson into framework or skill policy when it recurs, is domain-general, and can be stated without suppressing legitimate exploration. Put:

- Deterministic safety and lifecycle behavior in framework code.
- Stable reasoning workflows in skills.
- Environment-specific commands in runtime manuals/adapters.
- Campaign-specific observations in roadmap and round artifacts.
- Current-turn instructions in prompts.

Do not make a one-off workaround into a universal rule. Validate new policy against at least one different domain or environment in thought, tests, or a small campaign before treating it as general.
