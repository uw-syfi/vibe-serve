# Qwen3.5-397B-A17B vLLM 4xB200 Input

Use:

- `--input examples/model-serving/qwen3.5-397b-a17b-4xb200-vllm`
- `--runs-dir /work/vibesys-runs`
- `--local`
- `--interface service`
- `--modal` for B200-backed runs

This input materializes a pinned vLLM source checkout into the candidate
workspace through `workspace.sources` (sources-only: no starter app is
seeded), which the candidate uses to author a Modal serving app for the large
hybrid Gated-DeltaNet/MoE model `Qwen/Qwen3.5-397B-A17B`. The optimization
target is fitting the model in FP8 across 4xB200 and maximizing MoE/decode
throughput under the standard completion workload.
