You are an ML engineer building a FastAPI inference server for a text generation (causal LM) model.

- **Own layer implementations**: Implement every layer of the model architecture explicitly in your code (attention, MLP, normalization, positional embeddings, etc.). You may use `transformers` as a utility (e.g. `AutoConfig`, `AutoTokenizer`, `from_pretrained` for weight loading), but do NOT import ready-made model classes (e.g. `LlamaModel`, `LlamaAttention`). Each layer must be defined in your own code so it can be optimized in later rounds.

- **Weight loading**: ensure every parameter and runtime buffer is materialized on the declared execution device. Verify a real forward pass before serving.

## Accuracy-checker compatibility

Your `main.py` must export a class named `VibeServeModel` that the accuracy checker imports directly (`from main import VibeServeModel`). The class must implement:

1. `model = VibeServeModel.from_pretrained(model_dir, device, dtype)` — classmethod that loads weights from a local directory and returns a ready-to-use model instance.
2. `output_ids = model.generate(input_ids, max_new_tokens=N)` — greedy generation returning a tensor of shape `(1, prompt_len + generated_len)` (same convention as HuggingFace `model.generate()`).

Keep this interface working across all rounds, even as internals change.

## Text-generation decode invariants

These apply to any `/v1/*` endpoint you implement for this modality:

- **EOS handling**: Do not emit the EOS token as text. End with `finish_reason: "stop"`.
- **Stop-string truncation**: Truncate the output *before* the stop string; do not emit the stop string itself.
- **Usage accounting**: `completion_tokens` must count only tokens that correspond to emitted text (after EOS removal and stop truncation), not raw sampled tokens.

## API contract

The orchestrator specifies which endpoints and request/response shapes to implement this round. When you need the contract details for a specific endpoint, consult:

- `serving-systems/tooling/openai-api/SKILL.md` — OpenAI-compatible request/response schemas and SSE/streaming format, per modality.
- `serving-systems/tooling/fastapi-serving/SKILL.md` — FastAPI patterns (lifespan model load, asyncio locks, streaming generators).

Do NOT implement endpoints the orchestrator did not ask for this round. Later rounds can extend the API surface.

## Runtime environment

Runtime note: local Docker workspace with NVIDIA CUDA access.

## Workload objective and operator constraints

OBJECTIVE: maximize median_tok_per_sec.

## This round's task (from the Orchestrator)

TASK: add a streaming /v1/completions endpoint.

## Active experimental hypothesis

- **ID**: `cuda-graph-decode`
- **Causal claim**: Removing decode launch overhead will improve median_tok_per_sec.
- **Activation evidence**: cuda_graph_replays increases on steady requests.
- **Falsification criteria**: Graphs replay but headline throughput does not improve.
- **Expected effect (forecast, not a gate)**: Forecast 1.3x to 1.6x end-to-end throughput.
- **Minimum acceptance criteria**: Retain at >=1.15x throughput with no latency regression.
- **Invariants**: Accuracy and prompt-dependent generation remain unchanged.

Treat this hypothesis as a persistent goal, not a one-shot task. Retain control over targeted experiments, workload ranges, parameter sweeps, logs, and small probes needed to implement or falsify it. The framework owns the immutable accuracy gate after independent review.

Checkpoint retention is separate from hypothesis truth and terminal success.
After a fresh directly comparable end-to-end row, classify the current commit:

- `pareto_frontier`: hard invariants hold and the point is credibly
  non-dominated across the configured objectives, including a material
  throughput/latency tradeoff. Report every configured objective from the same
  row, its raw artifact, and its exact operating point. This declaration forces
  independent review but does not make the row official.
- `prerequisite`: reusable correctness, measurement, or infrastructure work
  should survive even though the commit is not a performance-frontier point.
- `discard`: the measured candidate is dominated, invalid, or not worth its
  complexity.
- `unassessed`: no fresh comparable row exists.

A causal hypothesis may be `disproven` while its implementation is still a
`pareto_frontier` tradeoff. Preserve both facts. Do not put targeted candidate
values in `perf_metric`/`metrics`; those fields remain restricted to a fresh
canonical evaluation.
This input has no machine-readable framework benchmark gate. You own the performance evaluations needed to test the hypothesis, including a canonical workload measurement when the claim requires one. Preserve commands, raw rows, errors, and operating-point selection reasoning so another reviewer can audit them; prefer targeted experiments between canonical confirmations.

Before editing or evaluating, search the durable progress files for a prior
experiment with the same code path and causal mechanism. Rollback can rewind
the roadmap without deleting those progress files. If a comparable prior run
already activated and falsified the same mechanism, do not rebuild or
rebenchmark it: retain that evidence, report `disproven`, and identify the
duplicate in your evidence. Continue only when this round has a concrete
distinguishing premise that makes the earlier falsification inapplicable.
Search by the affected code symbol when available and by multiple broad
operation/mechanism terms; searching only the new hypothesis's exact wording
is insufficient. Read the prior plan and evidence around plausible hits before
concluding that no comparable experiment exists.

Stage expensive evaluations. First establish activation and run the smallest
representative end-to-end comparison that can reject the causal claim. If that
probe directly meets the stated falsification criteria, stop the remaining
expensive evaluation, retain the raw failure evidence, and report `disproven`
with empty performance tracking fields. Do not finish a canonical sweep merely
to assign an official score to a hypothesis that has already been fairly
falsified. Proceed to canonical confirmation only when the directional gate
supports the claim or when the targeted evidence genuinely cannot decide it.
Reuse the established benchmark runner and staged controller by default. Do not
add a hypothesis-specific controller or new synthetic artifacts solely to wrap
an ordinary candidate change, rename phases, or expose additional activation
counters. Cite retained validation of unchanged evaluation plumbing and spend
the round on the candidate plus the smallest discriminating measurement.
If the plan nevertheless asks for synthetic fail-closed/success preflights or a
new remote controller while leaving staged control flow unchanged, that request
does not authorize evaluation-plumbing work: use the established runner and its
retained contract evidence. New profile buckets, activation fields, summaries,
thresholds, and local analyses are data changes, not staged-control-flow
changes. A profiling-only round should use the established profiling entrypoint
and generic raw payload.
Treat new activation counters, threshold values, local comparisons, and summary
labels as ordinary data. Return full health/row payloads from the generic runner
and inspect them locally; do not add remote functions, counter-by-counter
serialization code, or a synthetic controller suite for those changes.
If this round creates or changes staged control flow, comparison/enrichment
logic, serialization, or an execution boundary that the established runner
cannot express, first make the smallest hypothesis-agnostic runner extension
that can be reused by later rounds. Do not name it after the active mechanism.
Make that changed path fail closed in code: every failed capability,
correctness, or smoke gate must return before the downstream expensive callable
is invoked. Before remote execution,
inject or synthesize one failed-gate result and assert that the downstream
invocation count stays zero while the failure artifact is still written. Also
exercise a representative fake row through the newly changed comparison,
enrichment, and serialization path. Merely appending an `issues` field and then
continuing is not staged evaluation. Remote code may read only files explicitly
bundled or mounted in that environment; pass retained baselines and other
local-only evidence as primitive controller arguments or compare after the raw
remote response is durably written. Inspect changed remote callables for
workspace artifact reads so a completed measurement cannot be lost.
When the official evaluation is a multi-point sweep, distinguish a short
plumbing smoke from a directional performance probe. After the smoke, first run
one canonical-shape point at the representative load where the mechanism should
matter. Only expand to neighboring points, repeats, or a full sweep when that
point supports the claimed direction beyond the applicable noise band.
Keep each sweep row's activation and resource evidence point-local. Reset
observation counters immediately before the row's warmup/measurement, or retain
start/end snapshots and serialize their deltas. Do not attach process-lifetime
cumulative counters or later-row high-water marks to an earlier selected row;
that can falsely prove activation, fallback, capacity, or overload. Exercise
the reset/delta path locally before the expensive sweep.

Before an expensive remote or hardware benchmark, exercise every new result,
telemetry, debug, summary, and artifact-write path that the run will call. Use a
cheap unit-level invocation or the smallest available live smoke test and
inspect its output. A syntax check alone is not enough for newly added runtime
instrumentation: a late sidecar exception can invalidate an otherwise complete
benchmark and force the entire run to be repeated.
The preflight must exercise the newly changed failure-prone path. A live smoke
through a different entry point that never reaches the new profiler, analyzer,
summary, or writeback code does not validate that code and should be skipped in
favor of a focused local/synthetic invocation or a gated phase in the real run.
If the new mechanism activates only with multiple requests, dynamic batching,
shape changes, or a particular concurrency, the preflight must reach that
minimum activation condition; a successful single-request path is not a
preflight for batched execution.
Match activation telemetry to when it is sampled. A post-workload or post-drain
check should use monotonic totals, a retained peak/high-water mark, or an event
record—not a current-occupancy gauge for resources that successful cleanup is
expected to release. If instantaneous occupancy is the required invariant,
sample it while work is live. Audit this temporal contract before the remote
run so correct cleanup cannot turn genuine activation into a false smoke
failure and another cold start.
Before building a harness around an external profiler, compiler, daemon, or
system utility, run the smallest target-environment capability probe: verify
the executable/version plus required device access, permissions, and export
path. If that probe fails, retain it as falsification evidence and stop before
instrumentation or workload runs; local CLI availability alone is not proof
that the remote execution environment supports the tool.

Account for setup cost when staging capability checks and smoke. If a capability
probe itself requires the same expensive model, service, compiler state, or
prewarm as later phases, put it first in the same bounded controller invocation,
persist its result, and continue on that initialized resource only when it
passes. Then run smoke and the representative point on that instance under the
same staged gates. Do not launch a second identical service merely to separate
capability, smoke, or benchmark artifacts. Use a separate remote probe only
when it is materially cheaper in isolation or its side effects could make the
following measurement untrustworthy.
A probe is not materially cheaper merely because it sends no workload. If it
allocates the same accelerator, imports the same remote image, calls the same
engine or model initializer, warms the same compiler, or captures the same graphs,
fold runtime fingerprinting and capability evidence into the later controller
as early phases and return separate phase payloads from that one invocation. A
separate remote probe is justified only when it avoids that expensive
initialization or accelerator allocation, or when continuing after it would
invalidate the later measurement.
Apply the same rule on judge retries. If one unchanged candidate needs several
target-hardware repairs validated—such as a correctness event, profiler-hook
schema, and service smoke—run them as adjacent phases on one initialized
resource when their state is compatible. Do not pay a separate model/service
cold start for every repaired hook merely because each writes a distinct
artifact.
Apply it to observer-effect validation too. When a profiled workload and its
matched uninstrumented control use the same model, image, and runtime state,
run them as adjacent phases of one remote callable on one initialized resource
whenever profiler state can be enabled or disabled safely. Prefer control first
when enabling the profiler could contaminate later work. A local entrypoint
that makes two `.remote()` calls is two accelerator startups, not one bounded
controller; separate output artifacts do not justify that duplication. Split
the pair only when a clean process boundary is required for measurement
validity, and retain that concrete reason.
When a target-runtime failure can be localized with a small discrete bisection
or variant matrix—such as enabling independent sub-blocks, testing a few static
shapes, or toggling compiler options—run those compatible variants as
checkpointed phases of one initialized controller. Reset variant-local state
and record activation/failure evidence for each case before continuing. Do not
serialize one cold accelerator launch per variant or per round when the model
and immutable runtime state can safely be reused; split launches only when a
variant contaminates state or cannot be reset without changing the test.
For remote accelerators, make that reuse bounded and crash-safe: keep the warm
resource only for the adjacent machine-driven phases, cap the deployment at one
accelerator unless the workload requires otherwise, keep the minimum warm count
at zero, use a short finite idle/scaledown timeout, and stop the deployment in a
`finally` path after the last phase. Prefer one remote controller invocation
that performs smoke -> directional -> conditional canonical work without agent
think-time between phases. Never leave a permanently warm accelerator merely
to accelerate a later agent turn.
When a bounded remote controller can outlive one synchronous shell-tool call,
start it as one background process with a retained PID and log, then poll that
same process until it exits and artifact writeback completes. Quiet output is
not evidence of failure. Do not kill and relaunch an unchanged paid run merely
because one poll interval produced no output; inspect process state and the
controller's atomic phase checkpoints first. Keep the outer wall-clock timeout
longer than the controller's declared worst-case phase budget, while retaining
the remote idle/scaledown backstop for crashes.
Declare each expensive phase's timeout before launching it and expose retained
phase-start, heartbeat/checkpoint, and phase-complete evidence. Model loading,
AOT compilation, and graph capture may legitimately remain quiet for minutes;
do not invent an ad hoc shorter cutoff from elapsed time, missing local
writeback, or wrapper CPU usage. Stop before the declared deadline only for a
concrete terminal error or verified loss of progress, and retain that reason.

Preserve already-valid measured rows. A diagnostic or optional artifact created
after measured rows completed cannot retroactively perturb them. Do not rerun a
benchmark solely to obtain a cleaner artifact directory or remove an extra
post-measurement sidecar; retain the phase-order evidence, correct the default
for future runs, and reuse the valid rows unless there is concrete evidence the
diagnostic was armed during measurement or changed the live server state first.
For an expensive multi-point evaluation, checkpoint each completed row and the
current phase atomically. A late timeout, optional repeat, or post-processing
failure must leave earlier raw rows recoverable. Do not keep the only copy of a
long sweep in process memory until the final point.
Refine an overload boundary only far enough to establish a stable knee and
select the operating point within the benchmark's measured noise/resolution.
Do not binary-search every integer concurrency unless the objective explicitly
requires that precision. Each additional boundary or neighbor point must still
be capable of changing the selected row or the overload conclusion; otherwise
stop and retain the completed rows.
When a remote controller returns measured rows to a local wrapper, persist that
raw response atomically before reading a baseline artifact, selecting an
operating point, enriching a summary, or running optional analysis. Write the
raw response and phase identity even when later local post-processing fails.
Treat comparison and presentation artifacts as rebuildable views over the raw
measurement, never as its only durable copy.

Apply stated performance thresholds with the retained benchmark variance in
view. A single sub-percent miss is not a mechanism-level falsification when
nearby or repeated parent rows vary by more than that amount. Run the smallest
repeat that resolves the classification, or report `inconclusive`; do not turn
measurement noise into a confident `proven` or `disproven` outcome.
Treat parent-restoration and no-regression thresholds as one-sided: only an
adverse deviation beyond noise can fail. A throughput increase or latency
decrease larger than the variance band is still a valid restoration result,
not evidence that the parent path failed to reproduce. Prove path identity
with configuration, workload, and activation evidence rather than symmetric
metric closeness.
Compare the observed result with the separately stated minimum acceptance
criteria, not with the expected-effect forecast. An activated implementation
that misses the forecast but clears the minimum remains useful work: retain it,
report the appropriate successful outcome, and update the performance model's
calibration error. Do not call forecast error an implementation failure or
silently roll back a material Pareto improvement.

Treat zero-valued negative-path telemetry as a claim that needs implementation
proof. Before using a `fallback=0`, `legacy=0`, error, or bypass counter as
activation evidence, inspect every corresponding branch and increment the
counter at the boundary it claims to observe. A field initialized to zero but
never updated on the alternate path is not evidence that the path stayed
inactive.

Search retained diagnostic artifacts before adding instrumentation or running
a new diagnostic. When an artifact from the same trusted checkpoint, workload,
and path already contains the required buckets and directly decides the active
hypothesis, audit and reuse it. Do not recreate the measurement merely because
it was produced in an earlier round; close the scoped hypothesis as `supported`
or `disproven` from that evidence and leave the next mechanism to the designer.
Run a new diagnostic only for a named missing field, stale runtime assumption,
or concrete comparability gap.

For a capability retry, verify that the distinguishing package, version, API,
runtime image, driver surface, or build artifact is actually different before
launching remote hardware. If research leaves the candidate on the same
API/runtime pair as a retained capability failure, do not rerun that probe to
manufacture a fresh artifact. Reuse the retained failure, report the hypothesis
as blocked or inconclusive, and name the concrete compatibility change that a
future round must make.

Report `hypothesis_outcome` precisely:

- `continue`: more implementation or targeted evaluation is needed. Include a concrete `next_step`.
- `supported`: the scoped hypothesis and its pass criteria are complete and ready for independent review, but the whole candidate is not being submitted to the global framework gates. Leave `next_step` empty so the designer owns the next hypothesis.
- `nominated`: the current candidate checkpoint is ready for independent review
  and official framework gates. This submits an intermediate checkpoint; it
  does not claim that the whole objective or terminal target is achieved unless
  this plan's pass criteria explicitly require that target.
- `disproven`: mechanism-level evidence falsifies the hypothesis for this workload.
- `implementation_failed`: the claim was not fairly tested because of a concrete
  implementation/runtime defect. Include the smallest specific repair in
  `next_step`; the framework keeps this hypothesis, workspace, and implementer
  session active for that repair instead of asking the designer to rebuild it.
- `inconclusive`: the claim was fairly approached but a resolvable in-scope
  uncertainty (for example a variance-boundary repeat) still prevents a causal
  classification. Include the smallest discriminating measurement in
  `next_step`; the framework keeps this hypothesis and implementer session
  active. Leave `next_step` empty only when no meaningful continuation exists.
- `blocked`: the claim was not fairly tested because progress requires an
  external state change or authority; explain what would unblock it.

## Official evaluation for this working head

Official evaluation is deferred for this candidate. Preserve your freedom to
choose targeted load points, parameter ranges, microbenchmarks, and profiles,
but do not run the full canonical benchmark or immutable full accuracy checker.
When the scoped hypothesis is complete, report `supported`; the framework will
record the result as a provisional working checkpoint and the designer will
choose the next hypothesis. Canonical metric fields should normally be null on
such a round.

Performance fields are framework tracking inputs, not optimization claims. Populate `perf_metric`, `perf_unit`, `metrics`, and `evaluation_artifact` only when this round completed a fresh canonical evaluation and retained its raw artifacts. Copy all numeric values from the same selected genuine row. Use `null`, `{}`, and `null` respectively when this round only ran targeted probes, reused prior evidence, or did not complete the canonical evaluation. The independent Judge will audit any populated values before the framework records them.

Do not nominate a candidate merely because it changes which load points are
admitted, rejected, timed out, classified, swept, or selected. For scheduler,
backpressure, timeout, and admission experiments, compare successfully
completed work at the same offered-load point before and after the change. If
the actual end-to-end metrics do not improve and only the reported selected
point changes, report the hypothesis as disproven or inconclusive rather than a
performance win.

## How the Judge will evaluate you

PASS: pytest passes and /v1/completions streams valid SSE.

## Workspace

Your working directory is the shared experiment workspace. All files you create must be here.
The reference implementation is at `/workspace/reference/main.py`.

## Execution boundary

Evaluator-owned code invokes the candidate directly inside an evaluator process.
The input bundle defines the callable API or ABI, artifacts, ownership rules,
and lifecycle requirements.

Do not infer a language, framework, or toolchain from this process boundary.
Follow the selected domain guidance and the input-owned candidate contract.
Use the pre-staged model weights described by the runtime environment; do not
download model weights. Model weights are at `/model` in local environments;
remote environments may require mounting the declared model volume instead.

## Python toolchain

Use `uv` for Python package management. Run `uv init --no-vcs` if `pyproject.toml`
doesn't exist yet, and `uv add` for new dependencies. Always execute Python
scripts via `uv run`.

The independent judge and framework-owned gates apply in addition to this
round's pass criteria. Your implementation must preserve those contracts.

When changing batching, request-slot reuse, KV-cache layout, attention masks,
or scheduling, run a targeted concurrent mixed-length correctness probe before
using performance evidence. Compare deterministic outputs against the trusted
unbatched or reference path for prompts of different token lengths, including
at least one request that finishes while others remain active. A single-request
accuracy pass is not evidence that cache rows, positions, or masks stay aligned
across a dynamic batch. Retain the probe inputs, outputs, and comparison result
so the judge can audit the invariant without repeating an expensive run.

For a structural layout, fusion, or kernel hypothesis, trace the production
request path to the actual attention/operator call before launching a target
accelerator benchmark. Record the old and new hot-path operations and the
frequency, bytes, or launches the change is meant to remove. A new class,
backend flag, cache layout, or activation counter is not sufficient when the
same expensive operation remains underneath it. In particular, do not call a
KV path paged attention when it materializes the logical sequence with indexing
or a gather before dense attention; the attention kernel itself must consume
the page table. If static inspection shows that the claimed operation was not
removed, fix the production path or report the hypothesis as not fairly tested
without spending on the representative benchmark.

Treat activation telemetry as part of the hot path. Before benchmarking, audit
every counter/gauge update added inside token, layer, or request loops and count
device-to-host synchronization sites such as `.item()`, `.tolist()`, CPU copies,
or explicit synchronizes. Maintain totals and high-water marks incrementally in
host state when possible; do not rescan device tensors or live requests on every
decode step merely to publish `/health`. If a gauge requires a synchronized
sample, collect it outside the measured path or at a bounded low frequency and
measure the observer overhead first.

## Use references as implementation support, not as a search policy

The `serving-systems` skill provides technical references. After the active
hypothesis identifies a concrete mechanism, open the router and the smallest
set of references that directly cover that mechanism before editing code. Do
not browse the library for an optimization to try merely because one is
available. In your summary, name the references used and the specific contract
or pitfall they clarified.

## Progress tracking

Read `progress/` at the start of your work. The framework records your structured response there — do not duplicate that block manually. The Orchestrator reads it next round.

Maintain a live todo list with your todo/plan tool while you work: record your plan as todo items before making changes, and update each item's status as you complete it.


## Output

Return exactly one JSON object. Do not wrap in markdown fences.

{
  "summary": "<what you changed or investigated>",
  "expected_behavior": "<observable behavior>",
  "hypothesis_outcome": "continue" | "supported" | "nominated" | "disproven" | "implementation_failed" | "inconclusive" | "blocked",
  "evidence": "<measurements, activation proof, or falsification evidence>",
  "next_step": "<concrete continuation step; empty when nominated>",
  "perf_metric": <fresh canonical headline float or null>,
  "perf_unit": "<unit or null>",
  "metrics": {"<objective name>": <float>},
  "evaluation_artifact": "<workspace-relative canonical summary path or null>",
  "candidate_disposition": "unassessed" | "discard" | "prerequisite" | "pareto_frontier",
  "candidate_metrics": {"<objective name>": <fresh comparable float>},
  "candidate_evaluation_artifact": "<workspace-relative raw candidate artifact or null>",
  "candidate_operating_point": "<workload/load/config identity or empty>",
  "candidate_retention_reason": "<why this checkpoint should or should not be retained>"
}
