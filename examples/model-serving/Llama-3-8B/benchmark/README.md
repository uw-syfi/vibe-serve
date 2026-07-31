# Benchmark

Benchmark inputs for Llama-3-8B. Run the benchmark client on the same host as
the server and use its loopback URL so external network variability is excluded
from TTFT, TPOT, and throughput.

Start the server, wait until its health endpoint succeeds, and keep that same
server process running for the entire sweep. Each benchmark invocation sends
four discarded warm-up requests by default before starting its measurement
clock. Use `--warmup-requests 0` only for debugging; canonical measurements use
the default warm-up.

The canonical candidate score is the highest sustainable tok/s before overload.
Start with this closed-loop sweep, keeping the other load parameters fixed at
every point:

```bash
for concurrency in 1 2 4 8 16 32 64 128; do
  python benchmark.py \
    --url http://127.0.0.1:8000 \
    --concurrency "$concurrency" \
    --duration 20 \
    --max-tokens 128 \
    --temperature 0 \
    --output-json "result-c${concurrency}.json"
done
```

Continue doubling concurrency until the sweep includes an overloaded point
beyond the best sustainable point. Then test intermediate concurrency values
between the last rising point and the first overloaded point, and repeat the
best point and its neighbors to confirm the boundary. Retain every result and
report tok/s, TTFT, and TPOT from the same peak-throughput concurrency and
repetition. A sweep that ends with throughput still more than 3% above the best
earlier point has not measured the peak.

Use these thresholds to identify a suspected overload point relative to the
best earlier point and the last confirmed sustainable point:

- any request failure or timeout;
- output throughput below 95% of the best earlier point; or
- output throughput no higher than 103% of the best earlier point while p99
  TTFT or p99 end-to-end latency exceeds 2x the last sustainable value.

Repeat the suspected point and its neighbors before classifying it. A single row
crossing one of these thresholds can be ordinary run-to-run noise.

The client sets its HTTP connection limit to the requested closed-loop
concurrency. Every result also reports `requested_workers`,
`client_max_connections`, `max_in_flight_requests`, and `max_active_streams` in
the `load_concurrency` block. Investigate a row when the observed maxima are
lower than the requested load; the load generator or server admission path may
be the bottleneck.

## Quick hypothesis check

After a canonical sweep has established a stable boundary, use the best point
and its nearest lower and higher neighbors for a fast directional comparison.
For example, if those levels are `80, 96, 112`, run:

```bash
for concurrency in 80 96 112; do
  python benchmark.py \
    --url http://127.0.0.1:8000 \
    --concurrency "$concurrency" \
    --duration 10 \
    --max-tokens 128 \
    --temperature 0 \
    --output-json "quick-c${concurrency}.json"
done
```

Replace the example levels with neighbors from the most recent valid canonical
sweep. Do not reuse a historical boundary after a change that may move it.
Quick-check results may validate a hypothesis but do not replace the full sweep
or update the canonical score. A change to scheduling, batching, precision, or
memory use can move the overload boundary; run the canonical protocol before
reporting a new peak.

## Runtime expectations

`--duration` controls how long workers start new requests. Requests already in
flight then drain, so each point takes at least the configured duration and can
run longer near overload. A sweep with `N` points therefore needs at least
`N * duration`, plus warm-up, drain, server startup, and readiness time. Keep
the server alive between points so startup is paid once and caches are not reset.
