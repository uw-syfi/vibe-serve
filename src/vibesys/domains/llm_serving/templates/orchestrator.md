## Evidence-led optimization method

Choose from measured end-to-end evidence, not technique popularity. Quantify
the reference gap and each mechanism's defensible gain. While the gap exceeds
2x, prefer material bottlenecks or needed prerequisites over single-digit tweaks.

{% if workspace_sources %}
Treat the pinned checkout(s)—{% for source in workspace_sources %}`{{ source.dest }}/` ({{ source.name }}){% if not loop.last %}, {% endif %}{% endfor %}—as the implementation starting point. Direct the implementer to inspect and adapt their relevant paths; require concrete evidence before replacing seeded components.
{% endif %}

At the first baseline, after architecture change, and on a plateau, recommend
`serving-systems/references/tooling/performance-modeling.md`. Build a ranged whole-decode
roofline: all decode-touched weight/KV bytes, dense projection/MLP/output FLOPs,
useful tokens per step, attainable H100 bandwidth/compute, and service margin.
Reconcile the current-architecture ceiling with end-to-end wall time and an observer-controlled profile.
Reference-engine performance is an achievability check, not the hardware ceiling.

Translate terminal throughput into required step time and useful active batch.
Compare that demand with measured admission, graph buckets, KV capacity, memory,
and the latency gates. Queued concurrency is not useful model work. If the
current architecture cannot jointly reach throughput and latency targets, rank
a capacity or structural runtime/device change alongside kernel work.

For every plan, require production-path activation tied to the claimed removed
operation, frequency, bytes/launches, or boundary. A KV-layout change that still
reconstructs dense logical KV before dense attention is not paged-attention
compute. Telemetry inside token/layer/request loops must not add synchronizing
`.item()`, `.tolist()`, CPU copies, or rescans large enough to falsify itself.

Streaming is part of the measurement contract: preserve the benchmark's model-
token accounting and one logical delta record per generated model token even
when writes are coalesced. Scope API work to the endpoint named by the plan and
its authoritative API reference. Live exact cohorts may share contemporaneous
model work; completed output/token replay for later arrivals is model bypass,
not engine work.
