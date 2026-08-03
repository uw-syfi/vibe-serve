## LLM-serving profile capture

Capture the benchmark's steady-state production path. For a one-process
profiler, run the server under it and drive load from another shell. Discover
flags with `--help`; benchmark CLIs need not share token/request/rate flags.

For a local service: read objective, contract, manifest, and declared lifecycle;
identify executable, port, and ownership without assuming filename/language;
stop only identified stale processes; prewarm model/kernels; profile the server;
drive a short representative declared benchmark; then stop and analyze it.

For a compatible in-process adapter, the reference torch harness is:

```
python torch_profiler/analyze_torch_profile.py capture \
  --model-dir /workspace --weights-dir /model \
  --output /tmp/prof.json \
  --warmup 3 --num-iters 20 --max-tokens 32 \
  --prompt "The capital of France is"
```

Use it only when `VibeServeModel.from_pretrained(...)` and `.generate(...)`
exercise the reviewed production mechanism. It captures device kernels, not
HTTP, admission, scheduling, queueing, or service batching; do not extrapolate
without end-to-end evidence or recreate the production hot path just for it.

On Modal, discover the candidate's bounded remote controller/profile command
from runtime/build configuration. Do not require a fixed Python module,
decorator, or entrypoint, or retain Python solely for profiling. Return
analyzer-compatible JSON. If the profiler cannot observe the selected substrate
or service mechanism, report the capability gap rather than substitute a
batch-1 or compatibility-adapter profile.

Run Modal jobs for the same app serially. Never launch benchmark, wrapper
capture, and fallback concurrently: they can consume multiple GPUs, steal app
labels, and make writeback ambiguous. Observe a definite completion/failure
before fallback.
