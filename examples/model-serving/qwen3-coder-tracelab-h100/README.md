# qwen3-coder-tracelab-h100

TraceLab-shaped vLLM serving target for `Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8` on Modal H100.

Use:

- `--ref examples/model-serving/qwen3-coder-tracelab-h100/reference`
- `--acc-checker examples/model-serving/qwen3-coder-tracelab-h100/accuracy_checker`
- `--bench examples/model-serving/qwen3-coder-tracelab-h100/benchmark`

The benchmark replays real TraceLab public coding-agent sessions through
TraceLab's own `session_runner` instead of independent chat requests. The
TraceLab replay implementation is an ordinary evaluator source, materialized at
`_evaluator/tracelab-replay` and visible to optimization agents.

Start an optimization run with:

```bash
uv run vibesys \
  --input examples/model-serving/qwen3-coder-tracelab-h100 \
  --runs-dir "$PWD/exp_env" \
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
