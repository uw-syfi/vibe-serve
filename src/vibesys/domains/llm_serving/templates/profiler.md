## LLM-serving profile capture

Use the benchmark's steady-state serving path when collecting profile evidence. If the profiler strategy supports only one process, run the server under the profiler and drive load with the benchmark in a second shell. Discover flags with `--help`; do not assume every benchmark accepts the same request-count or token flags.

For local server-style captures, the usual shape is:

1. Read the objective, candidate contract, manifest, and declared startup and
   benchmark commands to identify the production executable, port, and process
   ownership. Do not assume a filename, language, or launcher.
2. Stop only stale candidate processes identified from that declared lifecycle.
3. Pre-warm — first-time kernel compilation or model load can take minutes.
4. Start the candidate server under the profiler.
5. Drive load using the benchmark command{% if benchmark_command %} (`{{ benchmark_command }}`){% endif %}. Use `--help` to find a short representative workload and output flag; do not assume every benchmark accepts the same rate, request-count, or token flags.
6. Stop the profiled server and analyze the report.

For torch in-process captures, the reference harness is designed around `VibeServeModel.from_pretrained(...)` and `.generate(...)`:

```
python torch_profiler/analyze_torch_profile.py capture \
  --model-dir /workspace --weights-dir /model \
  --output /tmp/prof.json \
  --warmup 3 --num-iters 20 --max-tokens 32 \
  --prompt "The capital of France is"
```

Use this mode only when the candidate retains a compatible in-process adapter
and that adapter exercises the mechanism under review. It provides
device-kernel-level evidence but does not cover HTTP, admission, scheduling, or
queueing overhead, so do not extrapolate it to the full service without an
end-to-end measurement. Do not recreate the production hot path in the adapter
merely to satisfy this helper.

For Modal torch profiling, discover the candidate's declared bounded remote
controller or profiling command from its runtime/build configuration. Do not
require a fixed Python module, decorator, or local entrypoint, and do not retain
a Python hot path solely to satisfy the profiler. The remote command must return
analyzer-compatible JSON. An in-process device microprofile does **not**
exercise HTTP, scheduler, admission, or multi-request batching unless the
candidate explicitly implements a live-service profiling path. If the selected
profiler no longer supports the candidate substrate or the requested
service-level mechanism, report that capability gap instead of presenting a
batch-1 or compatibility-adapter profile as production-path evidence.

Run Modal jobs for the same app serially. Do not launch a benchmark, wrapper
capture, and direct-function fallback concurrently: they can steal the same app
label, consume multiple GPUs, and make artifact writeback ambiguous. Monitor the
first dispatch to completion or a definite failure before choosing a fallback.
