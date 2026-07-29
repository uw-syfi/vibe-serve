# Qwen3-8B L40S ExactMap input

This bundle builds a custom Qwen3-8B serving candidate for one NVIDIA L40S. It
starts from the correctness-first ExactMap starter and evaluates the candidate
through its public HTTP API.

The current default candidate is `exactmap-triton-v1`: an explicit Qwen3
executor with digest-verified Safetensors loading, an owned 16-token KV page
pool, page-table-aware L40S Triton decode attention, fused normalization, and
fused gated activation. A decode-first scheduler forms batches across as many
as 16 active sequences and owns their page lifecycle. It does not call another
serving engine. The concurrency-16 implementation still requires an L40S smoke
and evaluator run before any performance claim.

Use:

- `--ref examples/model-serving/qwen3-8b-l40s-exactmap/reference`
- `--acc-checker examples/model-serving/qwen3-8b-l40s-exactmap/accuracy_checker`
- `--bench examples/model-serving/qwen3-8b-l40s-exactmap/benchmark`

Recommended VibeSys invocation:

```bash
./vs \
  --outer-loop agent \
  --backend cuda \
  --interface service \
  --input examples/model-serving/qwen3-8b-l40s-exactmap \
  --modal \
  --modal-gpu L40S \
  --profiler torch \
  --git-tracking
```

The default benchmark corpus is for tuning only. A later PIQ campaign must use
fresh runs and its independently sealed evaluation prompts.
