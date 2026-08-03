## LLM-serving implementation invariants

Trace every performance claim through the real request-to-model-to-stream path.
Prove the intended attention, graph, batching, transport, or KV mechanism runs;
a configured object, import, counter initialized to zero, or available backend
is not activation. Record point-local useful batch/tokens, selected kernel/path,
fallbacks, graph bucket, and resource limits without hot-loop synchronization.

For candidate components that use Python, use `uv`; this is not a requirement that the serving hot path, scheduler, transport, or kernels remain Python.

Keep correctness and workload shape fixed. Preserve prompt-dependent generation,
cache/mask/position alignment, deterministic greedy output where required, and
one logical streaming delta per generated model token. Coalescing writes is
allowed; changing the benchmark's token-record accounting is not.

Use the existing benchmark/controller path. Extend it only when the hypothesis
changes staged control flow or serialization, then prove injected failure makes
zero paid calls and one synthetic success traverses the new path. Capture exact
candidate source/build inputs before launch, retain each completed row
immediately, and run compatible control/candidate phases on one initialized
server when valid.

## Use references as implementation support

Load only narrow serving-systems references named by the plan or newly justified
by evidence—for example API format, async scheduling, continuous batching,
attention backend, CUDA graphs, or performance modeling. Do not preload the
entire serving library and do not retain FastAPI, Python, or an incumbent module
boundary unless the external contract requires it.
