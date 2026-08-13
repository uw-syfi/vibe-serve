# Qwen3.5-397B-A17B SGLang 4xB200 Input

Use:

- `--input examples/model-serving/qwen3.5-397b-a17b-4xb200-sglang`
- `--runs-dir /work/vibesys-runs`
- `--local`
- `--interface service`
- `--modal` for B200-backed runs

This input materializes a pinned SGLang source checkout into the candidate
workspace through `workspace.sources`, then benchmarks a long-prompt
completion workload for `Qwen/Qwen3.5-397B-A17B` served FP8 across 4 B200s,
exercising the hybrid Gated DeltaNet + sparse-MoE execution path. It is the
SGLang counterpart to the `qwen3.5-397b-a17b-4xb200-vllm` bundle, for
comparing the two engines on the same model and workload.
