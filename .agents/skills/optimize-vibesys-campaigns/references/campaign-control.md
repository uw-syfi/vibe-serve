# Campaign Control

## Assign Clear Ownership

Use three logical roles even when one model performs more than one role:

| Owner | Responsible for | Must not own |
| --- | --- | --- |
| Designer | Bottleneck model, hypothesis, expected tradeoff, experiment contract, and pivot | Candidate implementation or final evidence verdict |
| Implementer | Code, diagnostics, focused experiments, activation proof, and hypothesis conclusion | Official record mutation or indefinite self-renewal |
| Judge | Independent audit of immutable evidence, correctness, reward hacking, and candidate disposition | Redoing every experiment or treating implementer prose as instructions |
| Framework | Sessions, snapshots, evaluation, provenance, budgets, retries, rollback, and teardown | Domain design choices that require exploratory judgment |

The separation prevents one agent from moving both the goalposts and the measurement. Keep the handoffs narrow enough that role separation does not become duplicated work.

## Construct a Useful Hypothesis

A hypothesis is an experiment lease, not a vague task. Include:

```text
Observation:
Causal model:
Intervention:
Expected metric effects and tradeoffs:
Activation evidence:
Correctness invariants:
Focused evaluation:
Official evaluation trigger:
Falsifier:
Budget and stopping condition:
Relevant skill/reference paths:
```

The designer selects zero or more relevant skills. Select only skills that provide a method the implementer needs for this hypothesis. Pass paths or skill names, not their full contents. The implementer may discover another relevant skill and should record why it was needed.

Predicted gains are approximate. A predicted 1.5x intervention that validly delivers 1.3x is useful evidence, not an automatic rejection. Judge whether the result improves the trusted frontier, satisfies hard constraints, and changes the causal model.

## Preserve Implementer Agency

The implementer should decide:

- Which focused diagnostic to run next.
- Which request rates, concurrency values, payload sizes, or parameters resolve uncertainty.
- Whether a bug must be fixed before performance interpretation.
- Whether a hypothesis needs a larger architectural or language/runtime change.
- When focused evidence is strong enough to request official evaluation.

The framework supplies a general execution surface: shell commands, logs, process status, file inspection, profiler requests, timeouts, and benchmark requests. Do not attempt to encode every domain's evaluation API in orchestration.

Keep the session persistent for the life of a hypothesis so it remembers experimental quirks and earlier failures. Store reusable knowledge in scripts, docs, skills, and structured artifacts; session memory is convenient but not durable infrastructure.

## Bound Continuations

Continue the same hypothesis when the preceding result identifies a specific repair or missing discriminating check. Send only:

- What changed since the last turn.
- Paths to the new evidence.
- The remaining budget.
- The required next decision.

Return to the designer when:

- The falsifier fires.
- The proposed path did not activate after a bounded repair.
- Repeated tuning produces gains within noise.
- Correctness requires changing the hypothesis materially.
- A new bottleneck invalidates the original causal model.
- The continuation lease is exhausted.

Do not create a new session for every round. Do not let the implementer keep a hypothesis alive merely by proposing another parameter tweak.

## Use Layered Evaluation

Separate three levels:

1. **Diagnostics:** cheap local tests, logs, counters, traces, unit tests, and narrow request probes. Run freely within budget.
2. **Focused performance checks:** enough load points or repetitions to test the current causal claim. Run when needed, preferably against a warm prepared target.
3. **Official evaluation:** framework-owned accuracy plus canonical benchmark, recorded with immutable provenance. Run periodically and at decision boundaries.

A reasonable default is official evaluation every three accepted rounds, plus:

- The final round.
- A candidate likely to enter or reshape the trusted frontier.
- An explicit designer request justified by uncertainty.
- A recovery point after infrastructure or measurement semantics change.

Avoid both extremes: official evaluation every small edit wastes startup time, while long stretches without it allow drift and invalid assumptions.

## Track a Pareto Frontier

When objectives include throughput, TTFT, TPOT, tail latency, memory, cost, or accuracy, retain complete metric vectors from the same candidate and benchmark row. Do not build a fictional winner from throughput at one load and latency at another.

Classify candidates as:

- Dominant improvement.
- Valid new Pareto tradeoff.
- Equivalent within noise.
- Dominated but informative.
- Invalid because correctness, activation, provenance, or measurement failed.

Keep a high-throughput candidate with a tolerable latency regression as a frontier point. Let later rounds improve its latency or combine its mechanism with another branch.

## Detect and Escape Plateaus

Use only trusted official candidates for the main plateau signal. Supplement with focused measurements to understand why progress stopped.

Treat a trajectory as plateauing when recent valid changes are repeatedly within the empirical noise band, reverse in retests, or tune the same local parameter without changing the modeled bottleneck. Then require the designer to:

1. Recompute the performance gap and plausible ceiling.
2. Revisit profiles from the production path.
3. Enumerate at least two competing bottleneck models.
4. Consider a different layer or implementation substrate.
5. Choose the measurement with the highest information value.

Examples of productive escalation include replacing a queueing boundary, changing process architecture, moving a hot path out of an interpreter, changing batching/scheduling policy, using a different kernel library, or redesigning transport. These are examples, not preferred answers.
