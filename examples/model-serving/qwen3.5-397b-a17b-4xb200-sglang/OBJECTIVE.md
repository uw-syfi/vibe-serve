# Objective - Qwen3.5-397B-A17B SGLang 4xB200 Serving

Optimize an SGLang-based OpenAI-compatible server for `Qwen/Qwen3.5-397B-A17B`
on 4x NVIDIA B200 192GB GPUs. This is the SGLang counterpart of the vLLM
bundle `qwen3.5-397b-a17b-4xb200-vllm`: same model and workload, different
serving engine, for an engine comparison.

The candidate workspace starts from a pinned SGLang source checkout declared
in `vibesys.input.toml`. The candidate authors `main.py` to launch
`sglang.launch_server` across the 4 B200s and may edit SGLang internals,
launch flags, or dependency pins, while preserving the public API and
correctness gates.

Qwen3.5-397B-A17B is a sparse Mixture-of-Experts model: 397B total parameters,
~17B activated per token, 512 experts (10 routed + 1 shared per token), with
hybrid Gated DeltaNet (linear attention) layers interleaved with sparse-MoE
attention layers. It is natively multimodal via early fusion; this task
serves text-only completions. Native context is 262K tokens, extensible to
~1M. At bf16 the weights (~794 GB) do not fit across the 4 GPUs (768 GB
total); the implementation must serve FP8 (~397 GB), which fits with headroom
for KV cache.

The optimization surface spans tensor and expert parallelism across 4 GPUs
with all-to-all expert dispatch, FP8 grouped-GEMM MoE kernels on Blackwell,
managing both full-attention KV cache and Gated DeltaNet linear-attention
state, expert load balancing across 512 experts, and SGLang's RadixAttention
prefix cache and continuous batching under a workload with large (~2048-token)
prompts.

## Workload

Run the benchmark exactly as written unless the evaluator passes a different
`--url` or `--output-json`:

```bash
uv run python benchmark/benchmark.py --url <SERVER_URL> --output-json <PATH>
```

Default load:

- `/v1/completions`
- streaming responses
- closed-loop concurrency 48
- 90 second duration
- long synthetic prompts (~2048 tokens)
- `max_tokens = 512`
- `temperature = 0`

This benchmark stresses the MoE expert-dispatch path, the linear-attention
state alongside full KV cache, multi-GPU communication, scheduler overhead,
and decode throughput under concurrency with long prompts. Candidates must
not reduce prompt length, duration, concurrency, or max output tokens to
improve the score.

## Metrics

Pareto axes:

- `aggregate_throughput`: output tokens per second, maximize.
- `p99_latency_ms`: end-to-end request latency in milliseconds, minimize.

The scalar fallback/headline metric is `aggregate_throughput`.

## Correctness

The accuracy checker drives the running server over HTTP with three
reference-free gates: sentinel echo, known-answer, and greedy determinism. It
requires a real prompt-conditioned Qwen3.5-397B-A17B forward pass. Canned
responses, prompt echoing, skipped model execution, or non-deterministic
temperature-0 decoding fail the task.
