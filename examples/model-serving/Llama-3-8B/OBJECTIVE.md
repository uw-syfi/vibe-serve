# Objective — Llama-3-8B inference server

Serve Llama-3-8B on a single H100 with the best **throughput/latency trade-off**
under a realistic concurrent load, while keeping accuracy within the accuracy
checker's tolerance. Build an OpenAI-compatible `/v1/chat/completions` and
`/v1/completions` server.

This run is scored on a **Pareto frontier over two axes** (see `objectives.toml`),
both read from the benchmark's `--output-json` output:

- **`aggregate_throughput`** — output tokens/sec, **maximize**.
- **`p99_latency_ms`** — p99 end-to-end request latency in milliseconds, **minimize**.

A candidate is admitted to the frontier if it is non-dominated: at least as good
as the parent on both axes and strictly better on one. Raising throughput by
inflating tail latency (e.g. unbounded batch sizes) is a real trade-off, not a
free win — it moves you along the frontier, it does not dominate.

## Benchmark protocol — sweep to the overload boundary

The canonical score is the highest sustainable output-token throughput reached
before the server becomes overloaded. Measure it with a closed-loop concurrency
sweep. At every point, keep the request shape fixed with `--duration 20`,
`--max-tokens 128`, and `--temperature 0`. Do not use `--num-requests 1` or
shorten the output length: tiny workloads make throughput degenerate into
first-token latency and provide no useful batching or scheduling signal.

Wait for the server health endpoint before the sweep and keep the same server
process alive across every point. The benchmark sends four discarded requests
before each measured point. Canonical measurements keep this default warm-up so
one-time request-path initialization is excluded consistently.

Run the benchmark client on the same host as the server and send requests over
the loopback interface (for example, `http://127.0.0.1:<port>`). This keeps
external network routing and ingress variability out of TTFT, TPOT, and
throughput measurements while still exercising the OpenAI-compatible HTTP/SSE
serving path. Provision enough host CPU for the client so load generation does
not become the bottleneck.

Start at concurrency 1 and double through `2, 4, 8, 16, 32, 64, 128`. If
throughput at 128 is more than 3% above the best earlier point, continue doubling
until the sweep includes at least one overloaded point beyond the best
sustainable point, subject to the server's documented admission limit. A point
is overloaded when requests fail or time out, output throughput is below 95% of
the best earlier point, or output throughput is no higher than 103% of that best
point while p99 TTFT or p99 end-to-end latency exceeds 2x the last confirmed
sustainable point.
Confirm a suspected boundary by testing intermediate concurrency values between
the last rising point and the first overloaded point, then repeat the best point
and its neighbors. Do not classify ordinary run-to-run noise as overload.

Retain and report every sweep row. The canonical `aggregate_throughput` is the
highest value among non-overloaded points. Report TTFT, TPOT, and
`p99_latency_ms` from that same concurrency and repetition; do not combine
throughput from one operating point with latency from another. If the sweep
stops while throughput is still rising by more than 3%, it has not established a
peak and must not be reported as one.

The benchmark result's `load_concurrency` block must show that the client HTTP
connection limit is at least the requested worker count. Check its observed
`max_in_flight_requests` and `max_active_streams` values for client-side or
admission-path bottlenecks before treating a latency cliff as server overload.
Short targeted checks around a previously confirmed boundary are useful for
hypothesis testing, but they cannot replace this canonical sweep or establish a
new peak.

## Headline metric (`perf_metric`) and Pareto metrics — canonical fields, do not leave null

Headline metric: `aggregate_throughput` (output tok/s)

The scalar `perf_metric` (used for plateau detection and the scalar fallback) is
the peak sustainable **`aggregate_throughput`** selected by the concurrency
sweep. In addition, because this is a Pareto run, populate
`ProfilerSummary.metrics` with **both** objective values using these exact keys
from the selected operating point in the benchmark JSON:

```
metrics = {
  "aggregate_throughput": <benchmark JSON aggregate_throughput, float, tok/s>,
  "p99_latency_ms":       <benchmark JSON p99_latency_ms, float, ms>,
}
```

Also set `perf_unit = "tok/s"`. Read every value verbatim — do NOT derive,
invert, or substitute another field. Only set a metric to `null` if the server
never served a single successful request (the benchmark produced no data for
that field). Reporting `null` when a value was measured discards the run's
fitness and drops the candidate from the frontier.

## Notes

- Text-generation, dense causal LM. Hopper-class hardware assumed.
- Implement model layers explicitly (own attention / MLP / norm / RoPE); use
  `transformers` only as a utility for config / tokenizer / weight loading.
- Both the benchmark and the accuracy checker drive the **running server over
  HTTP** (no local model import). The accuracy checker enforces reference-free
  gates — sentinel-echo, known-answer, and greedy determinism — so a real
  prompt-conditioned forward pass is required; canned/echoed output fails.
