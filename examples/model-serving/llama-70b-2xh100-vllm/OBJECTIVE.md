# Objective - Llama-3.3 70B vLLM 2xH100 Serving

Optimize a vLLM-based OpenAI-compatible server for
`meta-llama/Llama-3.3-70B-Instruct` on 2x NVIDIA H100 80GB GPUs.

The candidate workspace starts from a pinned vLLM source checkout declared in
`vibesys.input.toml`. Modify vLLM internals, server launch code, or runtime
configuration as needed, while preserving the public API and correctness gates.

The model (~140 GB bf16) does not fit in one GPU. The implementation must serve
it across both GPUs (tensor parallelism, pipeline parallelism, or any other
strategy).

## Workload

Run the benchmark exactly as written unless the evaluator passes a different
`--url` or `--output-json`:

```bash
uv run python benchmark/benchmark.py --url <SERVER_URL> --output-json <PATH>
```

Default load:

- `/v1/completions`
- streaming responses
- closed-loop concurrency 8
- 30 second duration
- medium-length synthetic prompts (~256 words)
- `max_tokens = 128`
- `temperature = 0`

This benchmark stresses multi-GPU communication, weight sharding, KV-cache
management across devices, scheduler overhead, and decode throughput. Candidates
must not reduce prompt length, duration, concurrency, or max output tokens to
improve the score.

## Metrics

Pareto axes:

- `aggregate_throughput`: output tokens per second, maximize.
- `p99_latency_ms`: end-to-end request latency in milliseconds, minimize.

The scalar fallback/headline metric is `aggregate_throughput`.

## Correctness

The accuracy checker drives the running server over HTTP with three
reference-free gates: sentinel echo, known-answer, and greedy determinism. It
requires a real prompt-conditioned Llama forward pass. Canned responses, prompt
echoing, skipped model execution, or non-deterministic temperature-0 decoding
fail the task.
