# Qwen3.6-35B-A3B vLLM 2xH100 Input

Use:

- `--input examples/model-serving/qwen3.6-35b-a3b-2xh100-vllm`
- `--runs-dir /work/vibesys-runs`
- `--local`
- `--interface service`
- `--modal` for H100-backed runs

This input materializes a pinned editable vLLM checkout into the candidate
workspace through `workspace.sources`, then benchmarks a standard
chat/completion workload for `Qwen/Qwen3.6-35B-A3B` across 2 H100s. The model
fits one H100, so the optimization target is the sparse-MoE execution path
(routing, fused grouped-GEMM, bandwidth-bound decode) together with the
tensor-vs-expert parallelism strategy for the second GPU.
