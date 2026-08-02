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
- **Invariants**: Accuracy and prompt-dependent generation remain unchanged.

Treat this hypothesis as a persistent goal, not a one-shot task. Retain control over targeted experiments, workload ranges, parameter sweeps, logs, and small probes needed to implement or falsify it. The framework owns the immutable accuracy gate after independent review.
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
When the official evaluation is a multi-point sweep, distinguish a short
plumbing smoke from a directional performance probe. After the smoke, first run
one canonical-shape point at the representative load where the mechanism should
matter. Only expand to neighboring points, repeats, or a full sweep when that
point supports the claimed direction beyond the applicable noise band.

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
Before building a harness around an external profiler, compiler, daemon, or
system utility, run the smallest target-environment capability probe: verify
the executable/version plus required device access, permissions, and export
path. If that probe fails, retain it as falsification evidence and stop before
instrumentation or workload runs; local CLI availability alone is not proof
that the remote execution environment supports the tool.

Account for setup cost when staging that smoke. If service startup, model load,
compilation, or prewarm dominates and the harness can gate multiple phases on
one live instance, run the smoke first inside the same invocation and continue
to the representative point only when it passes. Do not launch a second
identical service merely to separate artifact directories. Use a separate
remote smoke only when it materially lowers expected cost or isolates state
that would make the following measurement untrustworthy.
For remote accelerators, make that reuse bounded and crash-safe: keep the warm
resource only for the adjacent machine-driven phases, cap the deployment at one
accelerator unless the workload requires otherwise, keep the minimum warm count
at zero, use a short finite idle/scaledown timeout, and stop the deployment in a
`finally` path after the last phase. Prefer one remote controller invocation
that performs smoke -> directional -> conditional canonical work without agent
think-time between phases. Never leave a permanently warm accelerator merely
to accelerate a later agent turn.

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

Search retained diagnostic artifacts before adding instrumentation or running
a new diagnostic. When an artifact from the same trusted checkpoint, workload,
and path already contains the required buckets and directly decides the active
hypothesis, audit and reuse it. Do not recreate the measurement merely because
it was produced in an earlier round; close the scoped hypothesis as `supported`
or `disproven` from that evidence and leave the next mechanism to the designer.
Run a new diagnostic only for a named missing field, stale runtime assumption,
or concrete comparability gap.

Report `hypothesis_outcome` precisely:

- `continue`: more implementation or targeted evaluation is needed. Include a concrete `next_step`.
- `supported`: the scoped hypothesis and its pass criteria are complete and ready for independent review, but the whole candidate is not being submitted to the global framework gates. Leave `next_step` empty so the designer owns the next hypothesis.
- `nominated`: the current candidate is ready for independent review and official framework gates.
- `disproven`: mechanism-level evidence falsifies the hypothesis for this workload.
- `implementation_failed`, `inconclusive`, or `blocked`: the claim was not fairly tested; explain why and what would unblock it.

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
  "evaluation_artifact": "<workspace-relative canonical summary path or null>"
}
