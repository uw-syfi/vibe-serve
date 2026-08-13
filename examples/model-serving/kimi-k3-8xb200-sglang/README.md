# Kimi-K3 SGLang 8xB200 Input

Use:

- `--input examples/model-serving/kimi-k3-8xb200-sglang`
- `--runs-dir /work/vibesys-runs`
- `--local`
- `--interface service`
- `--modal` for B200-backed runs

This input materializes a pinned editable SGLang checkout into the candidate
workspace through `workspace.sources`, then benchmarks a long-context,
long-decode completion workload for `moonshotai/Kimi-K3` (2.8T total / ~104B
active MXFP4 MoE, 896 experts) across 8xB200. This is a frontier-scale,
memory-bandwidth-bound serving problem; single-node 8xB200 sits at the edge
of the model's footprint and a multi-node deployment may be required for
long-context headroom.
