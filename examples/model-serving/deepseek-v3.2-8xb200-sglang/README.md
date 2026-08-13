# DeepSeek-V3.2 SGLang 8xB200 Input

Use:

- `--input examples/model-serving/deepseek-v3.2-8xb200-sglang`
- `--runs-dir /work/vibesys-runs`
- `--local`
- `--interface service`
- `--modal` for B200-backed runs

This input materializes a pinned editable SGLang checkout into the candidate
workspace through `workspace.sources`, then benchmarks a long-context
completion workload for the 685B-parameter FP8 MoE model
`deepseek-ai/DeepSeek-V3.2` (MLA + DeepSeek Sparse Attention) across 8xB200.
The workload is 8k-token prompts with 1k-token outputs, and the optimization
target spans FP8 grouped-GEMM MoE kernels on Blackwell, large-scale expert
parallelism across 256 experts, and MLA/DSA long-context KV-cache management;
building SGLang's Blackwell FP8 kernels is in scope.
