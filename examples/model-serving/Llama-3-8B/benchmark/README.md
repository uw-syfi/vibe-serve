# Benchmark

Benchmark inputs for Llama-3-8B. Run the benchmark client on the same host as
the server and use its loopback URL so external network variability is excluded
from TTFT, TPOT, and throughput.

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
repetition. A sweep that ends while throughput is still materially increasing
has not measured the peak.
