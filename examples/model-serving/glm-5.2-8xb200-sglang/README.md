# GLM-5.2 SGLang 8xB200 Input

Use:

- `--input examples/model-serving/glm-5.2-8xb200-sglang`
- `--runs-dir /work/vibesys-runs`
- `--local`
- `--interface service`
- `--modal` for B200-backed runs

This input materializes a pinned editable SGLang checkout into the candidate
workspace through `workspace.sources`, then benchmarks a long-context
completion workload for `zai-org/GLM-5.2` (753B total parameters, ~40B active
per token, FP8) across 8xB200. The optimization target is the DeepSeek-style
sparse-attention path for long context, FP8 grouped-GEMM MoE kernels on
Blackwell, and large-scale expert parallelism across all 8 GPUs, exercised by
an 8k-token-prompt / 1k-token-output workload.
