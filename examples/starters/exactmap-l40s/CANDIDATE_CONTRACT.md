# ExactMap candidate contract

## Runtime identity

The runtime product is `ExactMap`, version `0.1.0`, represented to PIQ as
`producer.engine=custom` plus runtime product identity `ExactMap`. It must never
claim to be vLLM, SGLang, or TensorRT-LLM.

The target is:

- `Qwen/Qwen3-8B`
- revision `b968826d9c46dd6066d109eabc6255188de91218`
- weight digest `sha256:8f51132290852a4ab4070da7e075f9a6e14f2e14553663e25211fdd99c170222`
- tokenizer revision `b968826d9c46dd6066d109eabc6255188de91218`
- BF16
- one NVIDIA L40S, compute capability 8.9
- maximum model length 16,384 tokens

## HTTP API

The service must provide:

- `GET /health`
- `GET /ready`
- `GET /version`
- `GET /server_info`
- `GET /v1/models`
- `POST /v1/completions`
- `POST /v1/chat/completions`

Both generation endpoints must support streaming server-sent events and
non-streaming JSON. A stream ends with `data: [DONE]`. The request contract
includes `model`, `max_tokens`, `min_tokens`, `temperature`, `top_p`, `top_k`,
`seed`, `stream`, and `stream_options.include_usage`. Chat requests additionally
include `messages` and `enable_thinking`.

Temperature zero is deterministic greedy decoding. `min_tokens` suppresses EOS
until the requested floor. Streaming usage must report exact prompt,
completion, and total token counts.

## Runtime introspection

`GET /server_info` is a strict, versioned snapshot. It reports:

- runtime product, version, and `engine=custom`
- `profileId=exactmap` and `profileVersion=exactmap.v1`
- model id, revision, weight digest, and tokenizer identity
- declared and realized runtime configuration
- configuration verification status
- immutable engine-build digest and locator when the candidate is sealed
- endpoint capabilities
- bounded request and token counters

An unsealed development candidate reports a null build digest and
`qualificationEligible=false`. It must not invent an artifact digest.

## Artifact

`python build_manifest.py` emits a canonical build manifest. The manifest binds
the ExactMap and VibeSys source revisions, model identity, target hardware,
toolchain, runtime configuration, search recipe, tuning corpus, build flags,
SBOM locator, and every selected artifact file digest. `engineBuildSha256` is
the SHA-256 of the canonical manifest body before that field is added.

VibeSys tuning prompts are optimization inputs only. They must not reuse the
sealed PIQ evaluation cohort.

## Disallowed shortcuts

The implementation fails review if it:

- invokes or proxies another serving engine
- changes evaluator-owned files
- fabricates token counts, timing, hardware identity, or artifact identity
- returns canned, echoed, or prompt-independent output
- weakens the API or accuracy contract to improve the benchmark
- silently reduces prompt length, output floor, concurrency, or request count
