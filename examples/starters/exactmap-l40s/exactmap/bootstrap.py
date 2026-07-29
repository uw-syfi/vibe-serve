from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .config import EngineConfig
from .types import GeneratedToken, GenerationInput, GenerationSession


class BootstrapTransformersEngine:
    """Correctness-first engine that the optimizer is expected to replace."""

    def __init__(self, config: EngineConfig) -> None:
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if not torch.cuda.is_available():
            raise RuntimeError("ExactMap requires an NVIDIA CUDA device")
        if config.kernel_family != "bootstrap-transformers":
            raise ValueError("bootstrap engine requires kernel_family=bootstrap-transformers")

        self._torch = torch
        self._transformers_version = transformers.__version__
        self._config = config
        self._tokenizer = AutoTokenizer.from_pretrained(
            config.tokenizer_source,
            revision=config.tokenizer_revision,
            trust_remote_code=False,
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            config.model_path,
            revision=config.model_revision,
            torch_dtype=torch.bfloat16,
            trust_remote_code=False,
        ).to("cuda")
        self._model.eval()
        self._ready = True

        eos_ids = self._model.generation_config.eos_token_id
        if isinstance(eos_ids, int):
            eos_ids = [eos_ids]
        self._eos_ids = tuple(int(token_id) for token_id in eos_ids or ())

    @property
    def ready(self) -> bool:
        return self._ready

    def _prompt_ids(self, request: GenerationInput) -> Any:
        if request.messages:
            messages = [{"role": role, "content": content} for role, content in request.messages]
            prompt = self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=request.enable_thinking,
            )
        elif request.prompt is not None:
            prompt = request.prompt
        else:
            raise ValueError("generation request has neither prompt nor messages")
        encoded = self._tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
        input_ids = encoded["input_ids"].to("cuda")
        if input_ids.shape[1] + request.max_tokens > self._config.max_model_len:
            raise ValueError("prompt plus max_tokens exceeds max_model_len")
        return input_ids

    def start(self, request: GenerationInput) -> GenerationSession:
        input_ids = self._prompt_ids(request)
        prompt_tokens = int(input_ids.shape[1])
        return GenerationSession(
            prompt_tokens=prompt_tokens,
            tokens=self._generate(input_ids, request),
        )

    def _sample(self, logits: Any, request: GenerationInput, generator: Any) -> int:
        torch = self._torch
        if request.temperature == 0:
            return int(torch.argmax(logits, dim=-1).item())

        scores = logits.float() / request.temperature
        if request.top_k > 0:
            threshold = torch.topk(scores, min(request.top_k, scores.shape[-1])).values[-1]
            scores = torch.where(scores < threshold, -torch.inf, scores)
        if request.top_p < 1:
            sorted_scores, sorted_indices = torch.sort(scores, descending=True)
            probabilities = torch.softmax(sorted_scores, dim=-1)
            cumulative = torch.cumsum(probabilities, dim=-1)
            remove = cumulative - probabilities > request.top_p
            sorted_scores = sorted_scores.masked_fill(remove, -torch.inf)
            scores = torch.full_like(scores, -torch.inf).scatter(-1, sorted_indices, sorted_scores)
        probabilities = torch.softmax(scores, dim=-1)
        return int(torch.multinomial(probabilities, 1, generator=generator).item())

    def _generate(self, input_ids: Any, request: GenerationInput) -> Iterator[GeneratedToken]:
        torch = self._torch
        past_key_values = None
        current_ids = input_ids
        generated_ids: list[int] = []
        previous_text = ""
        generator = torch.Generator(device="cuda")
        generator.manual_seed(request.seed)

        with torch.inference_mode():
            for index in range(request.max_tokens):
                outputs = self._model(
                    input_ids=current_ids,
                    past_key_values=past_key_values,
                    use_cache=True,
                )
                logits = outputs.logits[0, -1].clone()
                if index < request.min_tokens:
                    for eos_id in self._eos_ids:
                        logits[eos_id] = -torch.inf
                token_id = self._sample(logits, request, generator)
                if token_id in self._eos_ids and index >= request.min_tokens:
                    break

                generated_ids.append(token_id)
                decoded = self._tokenizer.decode(
                    generated_ids,
                    skip_special_tokens=False,
                    clean_up_tokenization_spaces=False,
                )
                text = (
                    decoded[len(previous_text) :] if decoded.startswith(previous_text) else decoded
                )
                previous_text = decoded
                yield GeneratedToken(token_id=token_id, text=text, index=index)
                current_ids = torch.tensor([[token_id]], device="cuda")
                past_key_values = outputs.past_key_values

    def observed_configuration(self) -> dict[str, object]:
        torch = self._torch
        properties = torch.cuda.get_device_properties(0)
        major, minor = torch.cuda.get_device_capability(0)
        return {
            **self._config.piq_configuration(),
            "kernel_family": "bootstrap-transformers",
            "gpu_name": properties.name,
            "target_compute_capability": f"{major}.{minor}",
            "cuda_version": torch.version.cuda,
            "torch_version": torch.__version__,
            "transformers_version": self._transformers_version,
        }
