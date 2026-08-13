# Qwen3-Coder vLLM Modal candidate

Repository-shaped VibeSys candidate for serving
`Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8` on one Modal H100.

The candidate implementation is `main.py`. Repository-native task definitions
live under `.vibesys/tasks/` and remain read-only to coding agents.

Run the TraceLab task from the VibeSys repository root:

```bash
vibesys \
  --project examples/model-serving/repositories/qwen3-coder-vllm-modal \
  --task qwen3-coder-tracelab-h100 \
  --runs-dir /work/vibesys-runs \
  --local \
  --modal \
  --modal-gpu H100 \
  --interface service
```

See the task README for its TraceLab submodule prerequisite and full command.
