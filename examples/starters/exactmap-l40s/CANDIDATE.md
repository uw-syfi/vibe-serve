# ExactMap L40S candidate starter

This workspace is the mutable implementation of ExactMap, a Qwen3-8B serving
runtime specialized for one NVIDIA L40S. The evaluator-owned workload and
accuracy checker are mounted separately and must not be edited.

The default server now uses the first optimized kernel implementation. It:

- streams the pinned Safetensors weights directly and verifies the canonical
  weight-manifest digest before loading them
- owns the Qwen3 prefill, decode, RoPE, attention, MLP, and sampling path
- preallocates an owned 16-token BF16 KV page pool with checked allocation,
  release, exhaustion, and per-sequence page tables
- uses a page-table-aware online-softmax Triton GQA kernel for single-token
  decode without materializing a contiguous attention matrix
- forms one decode launch across as many as 16 active sequences and admits
  new prefills only after servicing the current decode batch
- owns cancellation, cache release, page exhaustion, failure propagation, and
  scheduler shutdown for every submitted request
- fuses RMSNorm and residual normalization, plus SiLU and gate multiplication
- uses PyTorch scaled-dot-product attention for correctness-first prefill
- reports the realized kernel and KV identities through `/server_info`

This specialization reports `max_batch_size=16` and a 147,456-token shared KV
pool, sized for sixteen 1K-input plus 8K-output tuning requests. The bootstrap
Transformers executor remains available only as an explicit correctness oracle
with `--engine bootstrap`.

The next optimization stages are:

- chunked prefill where measurement shows a benefit
- CUDA graphs for stable decode batch shapes
- fused QKV projection and RoPE placement specialized for SM 89

Do not import, embed, shell out to, or proxy vLLM, SGLang, TensorRT-LLM, or
another serving engine. PyTorch, Triton, CUDA libraries, Transformers
configuration/tokenizer utilities, and Safetensors weight loading are allowed.

Run locally:

```bash
uv run python serve.py --engine kernel
```

The candidate contract is defined in `CANDIDATE_CONTRACT.md`.
