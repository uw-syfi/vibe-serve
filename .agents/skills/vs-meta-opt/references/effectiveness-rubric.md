# VS Meta Opt Effectiveness Rubric

## Contents

- Use Three Timescales
- Audit Categories
- Failure Classification
- Prefer a Dashboard Over One Reward
- Evidence Table

## Use Three Timescales

Judge each round using only the evidence available before its decision. Judge a recent window for trajectory quality. Judge across campaigns before claiming a policy is domain-general.

Do not equate candidate outcome with optimizer quality. Record both:

- **Frontier effectiveness:** whether trusted candidate quality improved.
- **Learning effectiveness:** whether the campaign reduced important uncertainty.

A failed candidate can be a good experiment. A strong candidate can result from luck or leaked prior knowledge.

## Audit Categories

### Outcome effectiveness

- Did VibeSys find and retain valid frontier candidates?
- Did selection preserve legitimate multi-objective tradeoffs?
- Were useful candidates rejected or invalid candidates promoted?
- How quickly did the trusted gap close?

### Learning effectiveness

- Did hypotheses have causal mechanisms, falsifiers, and decisive tests?
- Did negative results change the next decision?
- How many rounds repeated already-known information?
- How quickly did the campaign pivot after evidence invalidated a path?

### Operational efficiency

- Where did wall time and cost go by phase?
- Were builds, environments, model/data state, and remote resources reused safely?
- How much work was lost to hangs, retries, restoration, or missing fixtures?
- What was the cost per valid finding and frontier improvement?

### Coordination quality

- Did designer, implementer, judge, and framework have non-overlapping authority?
- Were evaluation or diagnosis runs duplicated?
- Did persistent sessions reduce rediscovery without creating unbounded leases?
- Were handoffs concise, path-based, and sufficient?

### Evidence integrity

- Were official results bound to exact candidate, evaluator, workload, and environment inputs?
- Were agent-run diagnostics kept distinct from framework-owned official evidence?
- Did evidence prove the claimed implementation path activated?
- Were reward-hacking risks detected without rejecting valid general techniques?

### Search quality and generality

- Did the designer target the measured gap rather than an assumed one?
- Did plateaus lead to genuinely different system hypotheses?
- Did prompts anchor the search to an incumbent implementation or environment?
- Were reusable domain methods supplied through selectively loaded skills?
- When independently reviewed, was VibeSys's next step competitive on evidence, impact, falsifiability, and cost?
- Would the system policy remain sensible for another domain?

### Other

Use `Other` for a material finding that does not fit cleanly above. Name the finding, explain why the existing categories are inadequate, and state whether it suggests a missing recurring category. Do not use `Other` to avoid making a clear classification.

## Failure Classification

Classify the limiting failure separately from the audit category:

| Failure | Meaning |
| --- | --- |
| Candidate | The candidate mechanism itself was ineffective |
| Implementation | The proposed behavior was not correctly realized |
| Activation | The behavior existed but the evaluated path did not exercise it |
| Evaluation | Measurement was invalid, incomplete, or incomparable |
| Infrastructure | Build, deployment, restoration, monitoring, or cleanup failed |
| Coordination | Role ownership caused duplication, conflict, or lost evidence |
| Context | Prompt or artifact routing was bloated, stale, or insufficient |
| Search policy | The loop chose low-information directions or pivoted poorly |
| Incentive | Rewards encouraged shortcuts or suppressed valid tradeoffs |
| Knowledge | Findings were not made durable or reusable |
| Other | Evidence supports a different mechanism; describe it explicitly |

Use multiple labels only when the causal chain genuinely spans them. Identify the earliest VibeSys-controlled cause.

## Prefer a Dashboard Over One Reward

Report at least one measure from each relevant dimension:

- Trusted frontier change per wall-clock time and accelerator cost.
- Valid information-producing rounds divided by total rounds.
- Median rounds from falsification or plateau to pivot.
- Duplicate official or focused evaluations.
- Agent inference tokens and elapsed time by role.
- Infrastructure failure and recovery rate.
- Invalid promotion and rejection counts.
- Hypotheses with activation and falsification evidence.
- Counterfactual review rate, proposal-gap rate, and later independent improvement after system changes.

Do not collapse these into one reward unless the tradeoffs and hard integrity constraints are explicit. A scalar can hide whether VibeSys learned efficiently or merely optimized the measurement.

## Evidence Table

Use a compact chronology:

| Round | Prior evidence | Decision | Candidate change | VibeSys change | Evidence produced | Validity | Time/cost | Information gained | Next decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

Link to artifacts instead of pasting logs. Mark missing fields. Treat role summaries as claims until reconciled with raw framework evidence.
