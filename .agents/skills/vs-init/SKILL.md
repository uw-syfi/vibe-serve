---
name: vs-init
description: Create or update a repository-native VibeSys LLM model-serving task for an existing candidate repository, Hugging Face model, hardware target, and optimization workload. Use when the user wants to add a named task below `.vibesys/tasks`, adapt a task-specific reference, accuracy checker, or benchmark, or start an optimization run without adding reusable framework code.
---

# VS Init

## Goal

Create a named task in an existing candidate repository:

```text
<project>/
├── .git/
├── .vibesys/
│   ├── evaluators.lock              # only when evaluator packages are used
│   └── tasks/
│       └── <task-name>/
│           ├── OBJECTIVE.md
│           ├── vibesys.input.toml
│           ├── README.md             # optional
│           ├── requirements.txt      # optional task-tool dependencies
│           ├── reference/            # optional reference and evaluation inputs
│           ├── accuracy_checker/     # optional task-specific checker
│           └── benchmark/            # optional task-specific benchmark
└── candidate source
```

The project root is both the candidate repository and the working directory for
agents, checkers, and benchmarks. Do not create a separate starter workspace or
declare `workspace.seed`. The model, hardware, and workload belong to the named
task; the serving implementation remains ordinary source in the existing
repository.

Prefer task-specific files. Create or extend a reusable evaluator package only
when multiple tasks or repositories share stable evaluator infrastructure.

The user should provide:

- Candidate repository path and serving framework.
- Hugging Face model id, plus a revision when reproducibility matters.
- Hardware target, such as H100, A100, Trainium, MacBook/MLX, or CPU.
- Natural-language workload goal, for example: "maximize output-token
  throughput for latency-insensitive batch jobs" or "minimize p95 latency for
  128-token chat completions at 8 requests/s."

If the repository, workload, or public API is unclear, ask a concise question
before writing files. Infer the model modality and a task name, then confirm
them when the user's request does not already make them explicit.

## Workflow

1. Resolve the candidate repository root. It must contain the source the agent
   will modify and be the root of its Git repository.
2. Read repository instructions, serving entry points, supported model
   registry, tests, and existing `.vibesys/tasks/` definitions.
3. Identify the workload's input-to-output modality and public API. Do not infer
   these from the model id alone. Use the model config/card, framework support,
   and the user's workload goal.
4. Propose, when confirmation is needed:
   - Inferred modality and API, such as text/chat to streamed text over the
     OpenAI chat-completions API.
   - Closest existing task whose checker or benchmark behavior can be adapted.
   - Task name, such as `qwen3-8b-h100-json` or
     `llama-3-8b-h100-high-concurrency`.
5. Select an adaptation source by modality and API first, workload second, and
   hardware third. Prefer a task already in the target repository.
6. Create `.vibesys/tasks/<task-name>/` in the candidate repository. Copy only
   files whose behavior is useful, then adapt every model, path, workload, and
   metric assumption.
7. Validate the task from the repository root and run the lightest checker and
   benchmark smoke tests that do not require an unrequested model download or
   expensive accelerator work.

Useful repository-native examples in this checkout include:

- `examples/model-serving/repositories/vllm/.vibesys/tasks/llama-3-8b-h100-long-prompts`
  for long shared prompts and short decode tails.
- `examples/model-serving/repositories/vllm/.vibesys/tasks/llama-3-8b-h100-high-concurrency`
  for open-loop concurrency and throughput.
- `examples/model-serving/repositories/vllm/.vibesys/tasks/llama-3-8b-h100-constrained-json`
  for schema-constrained output.
- `examples/model-serving/repositories/vllm/.vibesys/tasks/llama-70b-2xh100-vllm`
  for a multi-GPU vLLM workload.
- `examples/model-serving/repositories/vllm-omni/.vibesys/tasks/show-o2-1.5b-hq`
  for text-to-image HTTP serving.

When no task matches, create the smallest task-specific checker and benchmark
for the required HTTP, WebSocket, or in-process contract. Do not introduce a
framework abstraction merely to scaffold one task.

## Task Manifest

Use `vibesys.input.toml` and declare the domain. Direct command arrays are
relative to the candidate repository root, not the task directory:

```toml
version = 1

[agent]
domain = "llm-serving"

[accuracy]
command = [
  "uv", "run", "--no-project",
  "--with-requirements", ".vibesys/tasks/<task-name>/requirements.txt",
  "python", ".vibesys/tasks/<task-name>/accuracy_checker/checker.py",
]
timeout_seconds = 300

[benchmark]
command = [
  "uv", "run", "--no-project",
  "--with-requirements", ".vibesys/tasks/<task-name>/requirements.txt",
  "python", ".vibesys/tasks/<task-name>/benchmark/benchmark.py",
]
timeout_seconds = 600

[benchmark.result]
json_argument = "--output-json"
metric = "request_throughput"
```

Use `uv run --no-project` when task tooling should not modify or depend on the
candidate repository's environment. Omit `requirements.txt` and simplify the
command when no extra dependencies are needed.

Add `[benchmark.result]` only when one scalar metric is the authoritative
optimization objective. Its `metric` must exactly match a finite numeric field
in the benchmark JSON. Multi-objective tasks may instead define
`objectives.toml` beside the manifest.

Run and validate with the repository and task explicitly:

```bash
vibesys validate /path/to/project --task <task-name>
vibesys --project /path/to/project --task <task-name> \
  --backend cuda --interface service
```

The task may be omitted only when the repository has exactly one task.

## Modality Inference

Treat modality as a hypothesis until the workload or user confirms it.

Useful clues:

- `AutoModelForCausalLM`, `text-generation`, `chat`, `instruct`, or `code`:
  usually text to text.
- `response_format`, JSON schema, grammar, or constrained decoding: text and
  schema to structured text.
- `image-to-text`, `vision-language`, `vl`, or `qwen-vl`: image and text to
  text.
- `text-to-image`, diffusion, or Show-o: text to image.
- `automatic-speech-recognition`, `speech-to-text`, Whisper, or Moonshine:
  audio to text.
- `text-to-speech` or `tts`: text to audio.

Clarify models with multiple routes, base models used for specialized tasks,
and ids that describe only a format or quantization such as MLX, GGUF, AWQ,
GPTQ, or Neuron.

## Task Naming

Use a stable lowercase name containing letters, digits, dots, underscores, or
hyphens. Prefer concise hyphenated names:

```text
<model-family>-<size>-<hardware-or-workload>
```

Examples:

- `qwen3-8b-h100-chat`
- `llama-3-8b-trn2`
- `qwen3-32b-code-edit`
- `olmo-long-prefix-caching`

The name identifies a persistent workload definition, not a generated run.
Several model-support efforts in one repository should normally be several
named task directories.

## Objective

Write `OBJECTIVE.md` from the user's natural-language goal. Include:

- Candidate repository/framework and model.
- Serving modality and required public API.
- Hardware target.
- Workload shape, including request rate, concurrency, prompt/output lengths,
  batch assumptions, and streaming behavior when relevant.
- Primary metric and whether higher or lower is better.
- Correctness requirement and required checker behavior.
- Allowed implementation approaches and prohibited shortcuts.

Optimization presupposes correctness. State the runnable server and checker
contract first, then the performance objective. If the goal says "benchmark
X," name X as the headline metric rather than relying on profiler-only timing.

## Reference Inputs

Keep task reference material at
`.vibesys/tasks/<task-name>/reference/`. VibeSys does not relocate these files,
and coding agents receive `.vibesys/` read-only. Checker and benchmark scratch
output must go to `/tmp`, framework-owned runtime state, or a candidate build
directory outside `.vibesys/`.

For a Hugging Face model, make `reference/meta.json` the source of truth:

```json
{
  "model_id": "org/model-name",
  "revision": "full-revision-if-pinned",
  "task": "text-generation"
}
```

VibeSys materializes downloaded weights in runtime-owned cache/state and mounts
them for isolated execution. Do not create or write
`reference/model` during task setup. If the repository intentionally already
contains a valid `reference/model`, preserve it unchanged.

Use `reference/reference.py` only when a runnable oracle or explanatory model
implementation improves correctness validation. For generic causal LMs, prefer
`AutoTokenizer` and `AutoModelForCausalLM`. Set `trust_remote_code=True` only
when required and accepted.

## Accuracy Checker

Prefer a task-specific checker. For a causal LM, deterministic comparison
against Hugging Face outputs is a useful default:

- Use greedy generation with sampling disabled.
- Compare generated token ids before decoded text.
- Print the reference output, candidate output, and first differing token on
  failure.
- Exit zero only when every required case passes.

Match the checker to the actual service API and workload. Add targeted cases
instead of weakening correctness:

- Prefix caching: long shared prefixes with divergent suffixes.
- Code editing: representative buggy inputs and gold fixes or justified
  similarity thresholds.
- Constrained decoding: schema validation plus randomized sentinels that catch
  prompt-ignoring shortcuts.
- Streaming speech: chunk boundaries, finalization, and transcript semantics.
- Image or audio output: file/container validity plus task-appropriate semantic
  checks.

Expose explicit CLI options such as `--url`, `--model-dir`, or workload inputs
when useful. Test the checker from the repository root using the same command
shape declared in the manifest.

## Benchmark

For an OpenAI-compatible text-generation service, preserve useful generic
controls where relevant:

- `--url`, `--endpoint`, `--rate`, `--duration`, and `--num-requests`.
- `--max-tokens`, `--temperature`, `--prompt-len`, and `--seed`.
- Streaming SSE handling.
- TTFT, TPOT, total latency, request throughput, output-token throughput, and
  structured `--output-json` output.
- Poisson arrivals for open-loop load.

Adapt workload generation and the headline metric:

- Latency-sensitive serving: p50/p95/p99 latency or TTFT under a declared load.
- Latency-insensitive batch jobs: maximize aggregate or output-token throughput
  with a declared concurrency/batch regime.
- Prefix caching: repeated shared prefixes and controlled divergent suffixes.
- Long context: deterministic synthetic or dataset-backed prompt lengths.
- Predicted/code-edit output: preserve the request fields and quality contract
  used by that API.

Measure end-to-end public behavior unless the user specifically asks for an
in-process kernel or component benchmark. Do not make success depend on hidden
implementation details.

## Reusable Evaluator Packages

Keep one-off checker and benchmark code in the task. Use a versioned evaluator
package only when stable infrastructure is shared by multiple tasks or
repositories. Package-backed commands use logical entry points and require an
exact repository lock:

```toml
[evaluator]
name = "vibesys-evaluator-example"
version = "0.1.0"

[accuracy]
entrypoint = "vibesys-example"
args = ["check", "--workspace", "${PROJECT_ROOT}"]
```

Commit the corresponding `.vibesys/evaluators.lock`. Do not create a package
only to avoid a small task-specific script, and do not copy a reusable package
implementation into every task.

## README And Requirements

When useful, write a short task README with:

- The exact `vibesys --project ... --task ...` command.
- Model credentials, hardware, services, or submodules required.
- Lightweight checker and benchmark smoke-test commands from the repository
  root.
- Any explicit limitations of the correctness or performance measurements.

Keep task-tool dependencies isolated in the task's `requirements.txt`. Include
only what its reference, checker, and benchmark need, such as `transformers`,
`torch`, `httpx`, `datasets`, `jsonschema`, `soundfile`, or `websockets`.

## Validation

After creating or editing a task:

1. Run syntax checks on changed Python files with `python3 -m py_compile ...`
   when dependencies are unavailable.
2. Run lightweight `--help` or smoke checks using the manifest's repository-root
   command shape when imports allow it.
3. Parse changed JSON and TOML files.
4. Inspect the final task layout:

   ```bash
   find /path/to/project/.vibesys/tasks/<task-name> -maxdepth 3 -type f | sort
   ```

5. Validate without starting an agent or downloading model weights:

   ```bash
   vibesys validate /path/to/project --task <task-name>
   ```

6. Do not download large weights or run accelerator-heavy checks unless the
   user asks.
7. Check `git diff` and `git status` in the candidate repository. Task setup
   must not modify generated `.vibesys/state/` or unrelated source.

## Handoff

End with:

- Candidate repository and new task path.
- Source task or scripts adapted.
- Model, hardware, modality, workload, and optimization objective captured.
- Checks run and any checks deferred.
- Exact `vibesys --project ... --task ...` command to start the run.
