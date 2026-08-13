# Objective - Qwen3.5-397B-A17B vLLM 4xB200 Serving

Optimize a vLLM-based OpenAI-compatible server for `Qwen/Qwen3.5-397B-A17B` on
4x NVIDIA B200 192GB GPUs.

This input is sources-only: `vibesys.input.toml` seeds no starter workspace,
only a pinned vLLM source checkout via `workspace.sources`. The candidate
authors the Modal serving app and server launch code from that checkout, and
may modify vLLM internals, flags, and pins, while preserving the OpenAI API
and correctness gates.

Qwen3.5-397B-A17B is a sparse Mixture-of-Experts model: 397B total parameters
but only ~17B activated per token (512 experts, 10 routed + 1 shared). It uses
a hybrid architecture, mixing Gated DeltaNet (linear/recurrent attention)
layers with sparse MoE layers; only a subset of layers carry a standard KV
cache, and the Gated DeltaNet layers carry recurrent state instead. The model
is natively multimodal (early-fusion), but this task serves the text
`/v1/completions` path only. Native context is 262K tokens, extensible to
~1M.

At bf16 the weights (~794 GB) do not fit 4xB200 (768 GB total). The candidate
must serve the model in a lower-precision format such as FP8 (~397 GB), which
fits with headroom for KV cache and recurrent state; the specific quantization
approach and calibration/conversion strategy are the candidate's choice. The
implementation must also shard the model across all 4 GPUs (tensor
parallelism, expert parallelism, or a combination) and handle the
all-to-all expert-dispatch communication this requires.

This is a combined capacity- and MoE-efficiency workload: fitting weights
into HBM, expert routing/dispatch across 512 experts under a ~17B active
budget, FP8 grouped-GEMM MoE kernels on Blackwell, and managing both the
full-attention KV cache and the Gated DeltaNet recurrent state, all under
concurrent decode.

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

This benchmark stresses multi-GPU expert-parallel communication, FP8 MoE
kernel efficiency, KV-cache and recurrent-state management across devices,
scheduler overhead, and decode throughput under concurrency with large
prompts. Candidates must not reduce prompt length, duration, concurrency, or
max output tokens to improve the score.

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
