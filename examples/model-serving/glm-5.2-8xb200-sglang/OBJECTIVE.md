# Objective - GLM-5.2 SGLang 8xB200 Serving

Optimize an SGLang-based OpenAI-compatible server for `zai-org/GLM-5.2` on
8x NVIDIA B200 192GB GPUs.

The candidate workspace starts from a pinned SGLang source checkout declared
in `vibesys.input.toml`. Modify SGLang internals, server launch code, kernel
builds, or runtime configuration as needed, while preserving the public API
and correctness gates.

GLM-5.2 is a large sparse Mixture-of-Experts model: 753B total parameters,
~40B active per token, with a DeepSeek-style sparse attention mechanism
(`glm_moe_dsa`) for its 1M-token context window. At bf16 the weights (~1.5 TB)
do not fit 8xB200 (1536 GB total) with room for KV cache, so the candidate
must serve in FP8 (~753 GB). This requires building SGLang's Blackwell FP8
grouped-GEMM MoE kernels and bumping the CUDA toolchain to 12.8+ as needed.
The workload exercises the sparse-attention long-context path, large-scale
expert parallelism across all 8 GPUs (all-to-all dispatch, expert load
balancing), and KV-cache management for long contexts under concurrency, not
just raw weight sharding.

## Workload

Run the benchmark exactly as written unless the evaluator passes a different
`--url` or `--output-json`:

```bash
uv run python benchmark/benchmark.py --url <SERVER_URL> --output-json <PATH>
```

Default load:

- `/v1/completions`
- streaming responses
- closed-loop concurrency 64
- 120 second duration
- long synthetic prompts (~8192 tokens), distinct per request rather than a
  shared prefix
- `max_tokens = 1024`
- `temperature = 0`

This benchmark stresses the sparse-attention long-context path, FP8 MoE
dispatch across 8 GPUs, KV-cache management under concurrency, and decode
throughput. Candidates must not reduce prompt length, duration, concurrency,
or max output tokens, and must not collapse the distinct per-request prompts
into a single cached prefix, to improve the score.

## Metrics

Pareto axes:

- `aggregate_throughput`: output tokens per second, maximize.
- `p99_latency_ms`: end-to-end request latency in milliseconds, minimize.

The scalar fallback/headline metric is `aggregate_throughput`.

## Correctness

The accuracy checker drives the running server over HTTP with three
reference-free gates: sentinel echo, known-answer, and greedy determinism. It
requires a real prompt-conditioned GLM-5.2 forward pass. Canned responses,
prompt echoing, skipped model execution, or non-deterministic temperature-0
decoding fail the task.
