# Objective - ExactMap Qwen3-8B serving on one L40S

Build ExactMap, a custom OpenAI-compatible Qwen3-8B serving runtime optimized
for one NVIDIA L40S. The candidate must own the model execution path. It may use
PyTorch, Triton, CUDA libraries, Transformers configuration and tokenizer
utilities, and Safetensors weight loading. It must not invoke, embed, proxy, or
identify as vLLM, SGLang, TensorRT-LLM, or another serving engine.

## Frozen target

- Model: `Qwen/Qwen3-8B`
- Revision: `b968826d9c46dd6066d109eabc6255188de91218`
- Weight digest: `sha256:8f51132290852a4ab4070da7e075f9a6e14f2e14553663e25211fdd99c170222`
- Tokenizer revision: `b968826d9c46dd6066d109eabc6255188de91218`
- Precision: BF16
- Hardware: one NVIDIA L40S, compute capability 8.9
- Maximum model length: 16,384 tokens
- Runtime identity: `engine=custom`, `product=ExactMap`, version `0.1.0`
- PIQ configuration profile: `exactmap.v1`

## Optimization workload

The benchmark is a VibeSys tuning workload shaped like PIQ's controlled
`piq.reasoning.long-generation.1k8k.v1` profile:

- OpenAI-compatible streaming `/v1/chat/completions`
- closed-loop concurrency 16
- deterministic temperature-zero decoding
- approximately 1,024 input tokens
- minimum 4,096 and maximum 8,192 output tokens
- 32 measured requests after warmup

The benchmark creates its own deterministic tuning prompts. It must not read or
reuse a PIQ campaign prompt manifest or sealed evaluation cohort.

## Metrics

Pareto axes:

- `aggregate_throughput`: aggregate output tokens per second, maximize.
- `p99_latency_ms`: p99 end-to-end request latency, minimize.

The scalar headline metric is `aggregate_throughput`. Build time, artifact size,
and time to ready are reported separately and do not alter steady-state token
throughput.

Every measured request must succeed and produce at least `min_tokens`.
Candidates that shorten prompts, lower concurrency, lower the output floor,
fabricate token counts, or omit requests fail the benchmark.

## Correctness and API

The candidate must pass the evaluator-owned accuracy checker. It exercises:

- `/health`, `/ready`, `/version`, `/server_info`, and `/v1/models`
- streaming and non-streaming completions and chat completions
- exact stream usage accounting
- prompt-conditioned sentinel and known-answer behavior
- temperature-zero determinism
- `min_tokens` and thinking-mode behavior

`/server_info` must report declared and observed configuration. A development
candidate without a sealed artifact reports `qualificationEligible=false`;
inventing an artifact digest is a correctness failure.

The optimized implementation should replace the bootstrap Transformers model
execution with explicit Qwen layers, preallocated paged KV state, bounded
continuous batching, and L40S-specific kernels. The HTTP and artifact contracts
must remain stable throughout optimization.

## Claim boundary

A passing VibeSys run produces an optimization candidate. It does not produce a
PIQ measured-live recommendation. PIQ qualification requires a separately
preregistered, fresh ExactMap-versus-vLLM campaign using the sealed evaluation
cohort and an immutable ExactMap artifact.
