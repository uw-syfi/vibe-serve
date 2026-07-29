from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

MODEL_ID = "Qwen/Qwen3-8B"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
MODEL_WEIGHT_DIGEST = "sha256:8f51132290852a4ab4070da7e075f9a6e14f2e14553663e25211fdd99c170222"
TOKENIZER_REVISION = MODEL_REVISION


class EngineConfig(BaseModel):
    """Declared ExactMap runtime configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: Literal["Qwen/Qwen3-8B"] = MODEL_ID
    model_revision: Literal["b968826d9c46dd6066d109eabc6255188de91218"] = MODEL_REVISION
    model_weights_sha256: Literal[
        "sha256:8f51132290852a4ab4070da7e075f9a6e14f2e14553663e25211fdd99c170222"
    ] = MODEL_WEIGHT_DIGEST
    tokenizer_revision: Literal["b968826d9c46dd6066d109eabc6255188de91218"] = TOKENIZER_REVISION
    exactmap_version: Literal["0.1.0"] = "0.1.0"
    model_path: str = MODEL_ID
    tokenizer_path: str | None = None
    precision: Literal["bfloat16"] = "bfloat16"
    quantization: Literal["none"] = "none"
    tensor_parallel_size: Literal[1] = 1
    pipeline_parallel_size: Literal[1] = 1
    max_model_len: Literal[16_384] = 16_384
    max_batch_size: Literal[16] = 16
    max_num_batched_tokens: Literal[147_456] = 147_456
    kv_block_size: Literal[16] = 16
    kv_cache_dtype: Literal["bfloat16"] = "bfloat16"
    scheduler_policy: Literal["decode-first"] = "decode-first"
    chunked_prefill_size: Literal[0] = 0
    cuda_graph_batch_sizes: Literal["none"] = "none"
    kernel_family: Literal["exactmap-triton-v1", "bootstrap-transformers"] = "exactmap-triton-v1"
    target_gpu: Literal["NVIDIA L40S"] = "NVIDIA L40S"
    target_compute_capability: Literal["8.9"] = "8.9"
    engine_build_sha256: str | None = None
    artifact_locator: str | None = None

    @field_validator("engine_build_sha256")
    @classmethod
    def _validate_build_digest(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("engine_build_sha256 must be sha256:<64 lowercase hex>")
        return value

    @field_validator("artifact_locator")
    @classmethod
    def _validate_artifact_locator(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"oci://[^\s]+@sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("artifact_locator must be a digest-addressed oci:// locator")
        return value

    @property
    def tokenizer_source(self) -> str:
        return self.tokenizer_path or self.model_path

    def piq_configuration(self) -> dict[str, object]:
        return {
            "model_revision": self.model_revision,
            "model_weights_sha256": self.model_weights_sha256,
            "tokenizer_revision": self.tokenizer_revision,
            "exactmap_version": self.exactmap_version,
            "tensor_parallel_size": self.tensor_parallel_size,
            "pipeline_parallel_size": self.pipeline_parallel_size,
            "precision": self.precision,
            "quantization": self.quantization,
            "max_model_len": self.max_model_len,
            "max_batch_size": self.max_batch_size,
            "max_num_batched_tokens": self.max_num_batched_tokens,
            "kv_block_size": self.kv_block_size,
            "kv_cache_dtype": self.kv_cache_dtype,
            "scheduler_policy": self.scheduler_policy,
            "chunked_prefill_size": self.chunked_prefill_size,
            "cuda_graph_batch_sizes": self.cuda_graph_batch_sizes,
            "kernel_family": self.kernel_family,
            "target_compute_capability": self.target_compute_capability,
        }
