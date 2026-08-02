## LLM-serving profile capture

Use the benchmark's steady-state serving path when collecting profile evidence. If the profiler strategy supports only one process, run the server under the profiler and drive load with the benchmark in a second shell. Discover flags with `--help`; do not assume every benchmark accepts the same request-count or token flags.

For local server-style captures, the usual shape is:

1. Read `main.py` to understand startup and port.
2. Kill prior servers: `pkill -f "python main.py" 2>/dev/null || true; sleep 2`.
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

Use this mode for device-kernel-level evidence. It does not cover HTTP,
admission, scheduling, or queueing overhead, so do not extrapolate it to the
full service without an end-to-end measurement.

For Modal torch profiling, the implementer's `main.py` is required to expose `@app.local_entrypoint() modal_profile(output, num_iters, max_tokens, prompt)`. Invoke it from the editor container:

```
uv run modal run main.py::modal_profile \
  --output /workspace/prof.json \
  --num-iters 20 \
  --max-tokens 32 \
  --prompt "The capital of France is"
```

Modal local-entrypoint arguments are Click options: pass them directly, use
kebab-case, and do not insert a `--` separator. Run Modal through the workspace
environment (`uv run modal`), because importing `main.py` occurs locally before
dispatch.

This dispatches to a `@app.function profile_remote(...)` running on the Modal
GPU and returns analyzer-compatible JSON. The conventional implementation is an
in-process device microprofile; it does **not** exercise HTTP, scheduler,
admission, or multi-request batching unless the candidate explicitly implements
a live-service profiling endpoint. If the requested focus is one of those
service-level mechanisms and that endpoint is absent, report the contract gap
instead of presenting a batch-1 profile as production-path evidence.

Run Modal jobs for the same app serially. Do not launch a benchmark, wrapper
capture, and direct-function fallback concurrently: they can steal the same app
label, consume multiple GPUs, and make artifact writeback ambiguous. Monitor the
first dispatch to completion or a definite failure before choosing a fallback.
