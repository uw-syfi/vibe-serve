# Llama-3.3 70B vLLM 2xH100 Input

Use:

- `--input examples/model-serving/llama-70b-2xh100-vllm`
- `--interface service`
- `--modal` for H100-backed runs

This input materializes a pinned editable vLLM checkout into the candidate
workspace through `workspace.sources`, then benchmarks a standard
chat/completion workload for `meta-llama/Llama-3.3-70B-Instruct` across two
H100 GPUs.
