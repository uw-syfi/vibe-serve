# PIQ integration boundary

ExactMap is a product-scoped custom serving runtime, not an alias for an
existing engine:

| Owner | Responsibility |
| --- | --- |
| VibeSys | Search, compile, test, and seal an immutable ExactMap artifact. |
| ExactMap | Load that artifact, execute Qwen3-8B, and attest runtime state. |
| PIQ | Run governed comparisons and decide whether the exact artifact is eligible for a recommendation. |

## Runtime identity

The proposed PIQ identity is:

- `producer.engine=custom`
- runtime product `ExactMap`
- runtime version `0.1.0`
- configuration profile id `exactmap`
- configuration profile version `exactmap.v1`

PIQ should admit this combination through an ExactMap-specific registration.
Unknown `custom` products must remain fail-closed. A generic `custom` label is
not sufficiently precise for a Stack Fingerprint, replay pin, filter, report,
or Decision Record.

## Proposed `exactmap.v1` configuration

The registered profile should contain every performance-material value:

- tensor and pipeline parallel size
- precision and quantization
- maximum model length, batch size, and batched-token count
- KV block size and KV cache dtype
- scheduler policy and chunked-prefill size
- CUDA graph batch sizes and kernel family
- target compute capability
- model revision and weight digest
- ExactMap version
- `engineBuildSha256`

The serving adapter should capture `/server_info` immediately before and after
the measured window. The attested declared configuration must equal the
submitted configuration, and observed L40S identity must agree with the target.
Missing observation is `not-observed`, not a pass.

That distinction also explains the current `not-observed` TensorRT-LLM
configuration. The PIQ serving producer's runtime-verification module currently
defines protocols only for vLLM and SGLang and explicitly gives TensorRT-LLM no
runtime-configuration protocol. The producer therefore records every declared
TensorRT-LLM field as not observed. PIQ core has since gained a `trtllm.v2`
profile and a PyTorch `trtllm-serve` capture adapter, but that adapter is not
wired into the producer path that emitted the earlier run evidence.

PIQ may know values requested by a launcher, but it cannot claim they were
realized unless the live server reports or otherwise attests the material
runtime state. Declared configuration is evidence of intent. Observed
configuration is evidence of execution. ExactMap should add a closed
`exactmap:runtime-config:v1` producer protocol that strictly parses its
`/server_info` response before and after measurement.

## Artifact and replay

`engineBuildSha256` is the digest of the canonical manifest body before its
self-identifying field is added. The run artifact also carries the digest of
the complete manifest file. PIQ must bind both values. The manifest
transitively records source revisions, Qwen model identity and weights, the
L40S target, toolchain, runtime configuration, search inputs, selected
artifacts, and the SBOM locator.

PIQ replay should expose two distinct operations:

1. Deploy the exact digest-addressed artifact on a fresh same-shape resource.
2. Rebuild from the pinned VibeSys factory recipe and compare the resulting
   digest.

The second operation proves factory reproducibility. It is exact replay only
when it produces the identical `engineBuildSha256`.

## First governed claim

The first campaign should compare one immutable ExactMap artifact with fresh
vLLM evidence on the same pinned L40S host or a matched and counterbalanced host
block. It should preregister:

- the exact Qwen3-8B model and tokenizer revisions
- `piq.reasoning.long-generation.1k8k.v1`
- a sealed holdout that VibeSys did not use for tuning
- identical decoding, reset, cache, thermal, telemetry, and load conditions
- one primary performance metric
- hard correctness and success gates
- minimum effect, run count, and no-winner behavior

Build/search time, compile time, artifact size, and time to ready remain
separate from steady-state output-token throughput. A passing VibeSys result
is a candidate, and only the governed PIQ campaign can issue a measured-live
recommendation.
