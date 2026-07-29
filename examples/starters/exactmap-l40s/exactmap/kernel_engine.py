from __future__ import annotations

from typing import Any

from .config import EngineConfig
from .model import ExactMapQwen3
from .scheduler import ExactMapScheduler
from .types import GenerationInput, GenerationSession


class ExactMapKernelEngine:
    """Qwen3-8B execution owned by ExactMap's PyTorch and Triton path."""

    def __init__(self, config: EngineConfig) -> None:
        import torch
        import transformers
        from transformers import AutoTokenizer

        if not torch.cuda.is_available():
            raise RuntimeError("ExactMap requires an NVIDIA CUDA device")
        properties = torch.cuda.get_device_properties(0)
        capability = torch.cuda.get_device_capability(0)
        if "L40S" not in properties.name or capability != (8, 9):
            raise RuntimeError(
                "ExactMap exactmap-triton-v1 requires one NVIDIA L40S with capability 8.9"
            )

        self._torch = torch
        self._transformers_version = transformers.__version__
        self._config = config
        self._tokenizer = AutoTokenizer.from_pretrained(
            config.tokenizer_source,
            revision=config.tokenizer_revision,
            trust_remote_code=False,
        )
        self._model = ExactMapQwen3.from_pretrained(
            config.model_path,
            max_model_len=config.max_model_len,
            kv_block_size=config.kv_block_size,
            max_num_batched_tokens=config.max_num_batched_tokens,
            device=torch.device("cuda:0"),
            dtype=torch.bfloat16,
        )

        eos_ids = self._tokenizer.eos_token_id
        if isinstance(eos_ids, int):
            eos_ids = [eos_ids]
        self._eos_ids = tuple(int(token_id) for token_id in eos_ids or ())
        self._scheduler = ExactMapScheduler(
            self._model,
            self._tokenizer,
            max_batch_size=config.max_batch_size,
            eos_ids=self._eos_ids,
        )
        self._ready = True

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
        input_ids = encoded["input_ids"]
        if input_ids.shape[1] + request.max_tokens > self._config.max_model_len:
            raise ValueError("prompt plus max_tokens exceeds max_model_len")
        return input_ids

    def start(self, request: GenerationInput) -> GenerationSession:
        return self._scheduler.submit(request, self._prompt_ids(request))

    def close(self) -> None:
        self._ready = False
        self._scheduler.close()

    def observed_configuration(self) -> dict[str, object]:
        torch = self._torch
        properties = torch.cuda.get_device_properties(0)
        major, minor = torch.cuda.get_device_capability(0)
        try:
            import triton

            triton_version = triton.__version__
        except ImportError:  # pragma: no cover - the CUDA image requires Triton.
            triton_version = None
        return {
            **self._config.piq_configuration(),
            "kernel_family": "exactmap-triton-v1",
            "kv_layout": "layer-page-token-kvhead-headdim",
            "kv_page_count": self._model.page_pool.page_count,
            "kv_page_size": self._model.page_pool.page_size,
            "kv_pool_token_capacity": (
                self._model.page_pool.page_count * self._model.page_pool.page_size
            ),
            "scheduler_active_requests": self._scheduler.active_count,
            "scheduler_pending_requests": self._scheduler.pending_count,
            "attention_kernel": "exactmap-batched-paged-online-gqa-v1",
            "normalization_kernel": "exactmap-rmsnorm-v1",
            "mlp_kernel": "exactmap-silu-mul-v1",
            "gpu_name": properties.name,
            "target_compute_capability": f"{major}.{minor}",
            "cuda_version": torch.version.cuda,
            "torch_version": torch.__version__,
            "transformers_version": self._transformers_version,
            "triton_version": triton_version,
        }
