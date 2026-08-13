# Qwen3-Coder TraceLab H100 task

TraceLab-shaped vLLM serving target for `Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8` on Modal H100.

The benchmark replays real TraceLab public coding-agent sessions through
TraceLab's own `session_runner` instead of independent chat requests. Its
task-owned implementation lives under `benchmark/tracelab-replay` and is
read-only to coding agents.

Initialize the pinned TraceLab submodule before starting a run:

```bash
git -c submodule.examples/model-serving/repositories/qwen3-coder-vllm-modal/.vibesys/tasks/qwen3-coder-tracelab-h100/benchmark/tracelab-replay/tracelab.update=checkout \
  submodule update --init \
  examples/model-serving/repositories/qwen3-coder-vllm-modal/.vibesys/tasks/qwen3-coder-tracelab-h100/benchmark/tracelab-replay/tracelab
```

Start an optimization run with:

```bash
vibesys \
  --project examples/model-serving/repositories/qwen3-coder-vllm-modal \
  --task qwen3-coder-tracelab-h100 \
  --runs-dir /work/vibesys-runs \
  --local \
  --exp-name qwen3-coder-tracelab-h100 \
  --modal \
  --modal-gpu H100 \
  --agent-backend cli \
  --cli-provider codex \
  --backend cuda \
  --interface service \
  --modality text_generation \
  --profiler torch \
  --max-rounds 4 \
  --headless
```
