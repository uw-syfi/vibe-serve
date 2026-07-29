from __future__ import annotations

import gc
import hashlib
import heapq
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as functional
from huggingface_hub import snapshot_download
from safetensors import safe_open

from .config import MODEL_REVISION, MODEL_WEIGHT_DIGEST
from .kernels import (
    add_rms_norm,
    decode_paged_gqa_batch,
    rms_norm,
    silu_mul,
)


@dataclass(frozen=True)
class Qwen3Shape:
    hidden_size: int
    intermediate_size: int
    layer_count: int
    attention_heads: int
    kv_heads: int
    head_dim: int
    vocab_size: int
    rms_norm_eps: float
    rope_theta: float

    @classmethod
    def from_config(cls, value: Mapping[str, Any]) -> Qwen3Shape:
        shape = cls(
            hidden_size=int(value["hidden_size"]),
            intermediate_size=int(value["intermediate_size"]),
            layer_count=int(value["num_hidden_layers"]),
            attention_heads=int(value["num_attention_heads"]),
            kv_heads=int(value["num_key_value_heads"]),
            head_dim=int(value["head_dim"]),
            vocab_size=int(value["vocab_size"]),
            rms_norm_eps=float(value["rms_norm_eps"]),
            rope_theta=float(value["rope_theta"]),
        )
        if shape.hidden_size != shape.attention_heads * shape.head_dim:
            raise ValueError("Qwen hidden size does not match its query-head geometry")
        if shape.attention_heads % shape.kv_heads:
            raise ValueError("Qwen query heads must be divisible by KV heads")
        return shape


FROZEN_QWEN3_8B_SHAPE = Qwen3Shape(
    hidden_size=4096,
    intermediate_size=12288,
    layer_count=36,
    attention_heads=32,
    kv_heads=8,
    head_dim=128,
    vocab_size=151936,
    rms_norm_eps=1e-6,
    rope_theta=1_000_000.0,
)


@dataclass(frozen=True)
class AttentionWeights:
    q_proj: torch.Tensor
    k_proj: torch.Tensor
    v_proj: torch.Tensor
    o_proj: torch.Tensor
    q_norm: torch.Tensor
    k_norm: torch.Tensor


@dataclass(frozen=True)
class MLPWeights:
    gate_proj: torch.Tensor
    up_proj: torch.Tensor
    down_proj: torch.Tensor


@dataclass(frozen=True)
class LayerWeights:
    input_norm: torch.Tensor
    attention: AttentionWeights
    post_attention_norm: torch.Tensor
    mlp: MLPWeights


@dataclass(frozen=True)
class ExactMapWeights:
    embedding: torch.Tensor
    layers: tuple[LayerWeights, ...]
    final_norm: torch.Tensor
    lm_head: torch.Tensor

    @classmethod
    def from_state_dict(
        cls,
        state_dict: Mapping[str, torch.Tensor],
        shape: Qwen3Shape,
        *,
        device: torch.device,
        dtype: torch.dtype,
    ) -> ExactMapWeights:
        def tensor(name: str) -> torch.Tensor:
            try:
                value = state_dict[name]
            except KeyError as exc:
                raise ValueError(f"Qwen checkpoint is missing {name}") from exc
            return value.to(device=device, dtype=dtype).contiguous()

        layers = []
        for index in range(shape.layer_count):
            prefix = f"model.layers.{index}"
            layers.append(
                LayerWeights(
                    input_norm=tensor(f"{prefix}.input_layernorm.weight"),
                    attention=AttentionWeights(
                        q_proj=tensor(f"{prefix}.self_attn.q_proj.weight"),
                        k_proj=tensor(f"{prefix}.self_attn.k_proj.weight"),
                        v_proj=tensor(f"{prefix}.self_attn.v_proj.weight"),
                        o_proj=tensor(f"{prefix}.self_attn.o_proj.weight"),
                        q_norm=tensor(f"{prefix}.self_attn.q_norm.weight"),
                        k_norm=tensor(f"{prefix}.self_attn.k_norm.weight"),
                    ),
                    post_attention_norm=tensor(f"{prefix}.post_attention_layernorm.weight"),
                    mlp=MLPWeights(
                        gate_proj=tensor(f"{prefix}.mlp.gate_proj.weight"),
                        up_proj=tensor(f"{prefix}.mlp.up_proj.weight"),
                        down_proj=tensor(f"{prefix}.mlp.down_proj.weight"),
                    ),
                )
            )
        return cls(
            embedding=tensor("model.embed_tokens.weight"),
            layers=tuple(layers),
            final_norm=tensor("model.norm.weight"),
            lm_head=tensor("lm_head.weight"),
        )


class SnapshotWeightStore:
    """Resolve and stream a pinned Safetensors checkpoint one tensor at a time."""

    def __init__(self, model_path: str, revision: str) -> None:
        candidate = Path(model_path).expanduser()
        if candidate.exists():
            self.root = candidate.resolve(strict=True)
        else:
            self.root = Path(
                snapshot_download(
                    repo_id=model_path,
                    revision=revision,
                    allow_patterns=(
                        "config.json",
                        "model.safetensors",
                        "model.safetensors.index.json",
                        "model-*.safetensors",
                        "tokenizer.json",
                        "tokenizer_config.json",
                        "special_tokens_map.json",
                        "generation_config.json",
                        "merges.txt",
                        "vocab.json",
                    ),
                )
            )
        config_path = self.root / "config.json"
        if not config_path.is_file():
            raise ValueError(f"Qwen checkpoint has no config.json at {self.root}")
        config_value = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config_value, dict):
            raise ValueError("Qwen config.json must contain one JSON object")
        self.shape = Qwen3Shape.from_config(config_value)

        index_path = self.root / "model.safetensors.index.json"
        if index_path.is_file():
            index_value = json.loads(index_path.read_text(encoding="utf-8"))
            weight_map = index_value.get("weight_map") if isinstance(index_value, dict) else None
            if not isinstance(weight_map, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in weight_map.items()
            ):
                raise ValueError("Safetensors index has an invalid weight_map")
            self._weight_map = dict(weight_map)
        else:
            single_file = self.root / "model.safetensors"
            if not single_file.is_file():
                raise ValueError("Qwen checkpoint has no Safetensors weights")
            with safe_open(single_file, framework="pt", device="cpu") as handle:
                self._weight_map = {key: single_file.name for key in handle.keys()}

    def tensor(
        self,
        name: str,
        *,
        device: torch.device,
        dtype: torch.dtype,
    ) -> torch.Tensor:
        try:
            relative_path = self._weight_map[name]
        except KeyError as exc:
            raise ValueError(f"Qwen checkpoint is missing {name}") from exc
        path = (self.root / relative_path).resolve(strict=True)
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Safetensors shard escapes the checkpoint root: {path}") from exc
        with safe_open(path, framework="pt", device="cpu") as handle:
            value = handle.get_tensor(name)
        return value.to(device=device, dtype=dtype).contiguous()

    def verify_weight_digest(self, expected_digest: str) -> None:
        lines = []
        for relative_path in sorted(set(self._weight_map.values())):
            path = (self.root / relative_path).resolve(strict=True)
            try:
                path.relative_to(self.root)
            except ValueError as exc:
                raise ValueError(f"Safetensors shard escapes the checkpoint root: {path}") from exc
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            lines.append(f"{digest.hexdigest()}  {relative_path}\n")
        manifest = "".join(lines).encode()
        actual_digest = f"sha256:{hashlib.sha256(manifest).hexdigest()}"
        if actual_digest != expected_digest:
            raise ValueError(
                "Qwen checkpoint weight digest mismatch: "
                f"expected {expected_digest}, observed {actual_digest}"
            )

    def load(
        self,
        *,
        device: torch.device,
        dtype: torch.dtype,
    ) -> ExactMapWeights:
        def tensor(name: str) -> torch.Tensor:
            return self.tensor(name, device=device, dtype=dtype)

        layers = []
        for index in range(self.shape.layer_count):
            prefix = f"model.layers.{index}"
            layers.append(
                LayerWeights(
                    input_norm=tensor(f"{prefix}.input_layernorm.weight"),
                    attention=AttentionWeights(
                        q_proj=tensor(f"{prefix}.self_attn.q_proj.weight"),
                        k_proj=tensor(f"{prefix}.self_attn.k_proj.weight"),
                        v_proj=tensor(f"{prefix}.self_attn.v_proj.weight"),
                        o_proj=tensor(f"{prefix}.self_attn.o_proj.weight"),
                        q_norm=tensor(f"{prefix}.self_attn.q_norm.weight"),
                        k_norm=tensor(f"{prefix}.self_attn.k_norm.weight"),
                    ),
                    post_attention_norm=tensor(f"{prefix}.post_attention_layernorm.weight"),
                    mlp=MLPWeights(
                        gate_proj=tensor(f"{prefix}.mlp.gate_proj.weight"),
                        up_proj=tensor(f"{prefix}.mlp.up_proj.weight"),
                        down_proj=tensor(f"{prefix}.mlp.down_proj.weight"),
                    ),
                )
            )
            gc.collect()
        return ExactMapWeights(
            embedding=tensor("model.embed_tokens.weight"),
            layers=tuple(layers),
            final_norm=tensor("model.norm.weight"),
            lm_head=tensor("lm_head.weight"),
        )


class KVPagePool:
    """Owned physical KV pages shared by present and future active sequences."""

    def __init__(
        self,
        shape: Qwen3Shape,
        token_capacity: int,
        page_size: int,
        *,
        device: torch.device,
        dtype: torch.dtype,
    ) -> None:
        if token_capacity < page_size or token_capacity % page_size:
            raise ValueError("KV token capacity must be a positive multiple of page size")
        self.shape = shape
        self.page_size = page_size
        self.page_count = token_capacity // page_size
        cache_shape = (
            shape.layer_count,
            self.page_count,
            page_size,
            shape.kv_heads,
            shape.head_dim,
        )
        self.keys = torch.empty(cache_shape, device=device, dtype=dtype)
        self.values = torch.empty(cache_shape, device=device, dtype=dtype)
        self._free_pages = list(range(self.page_count))
        heapq.heapify(self._free_pages)
        self._owners: dict[int, int] = {}
        self._next_sequence_id = 0
        self._lock = threading.Lock()

    @property
    def free_page_count(self) -> int:
        with self._lock:
            return len(self._free_pages)

    @property
    def allocated_page_count(self) -> int:
        with self._lock:
            return len(self._owners)

    def new_sequence(self, max_model_len: int) -> PagedKVCache:
        if max_model_len > self.page_count * self.page_size:
            raise ValueError("sequence maximum exceeds the KV page pool")
        with self._lock:
            sequence_id = self._next_sequence_id
            self._next_sequence_id += 1
        return PagedKVCache(self, sequence_id, max_model_len)

    def acquire(self, owner: int, count: int) -> tuple[int, ...]:
        if count < 0:
            raise ValueError("page acquisition count cannot be negative")
        with self._lock:
            if count > len(self._free_pages):
                raise RuntimeError(
                    "ExactMap KV page pool exhausted: "
                    f"requested={count}, free={len(self._free_pages)}"
                )
            pages = tuple(heapq.heappop(self._free_pages) for _ in range(count))
            for page in pages:
                if page in self._owners:
                    raise RuntimeError("ExactMap KV allocator selected an owned page")
                self._owners[page] = owner
            return pages

    def release(self, owner: int, pages: tuple[int, ...]) -> None:
        with self._lock:
            for page in pages:
                if self._owners.get(page) != owner:
                    raise RuntimeError("ExactMap KV page release does not match its owner")
            for page in pages:
                del self._owners[page]
                heapq.heappush(self._free_pages, page)


class PagedKVCache:
    """Logical sequence state backed by a checked physical page table."""

    def __init__(
        self,
        pool: KVPagePool,
        sequence_id: int,
        max_model_len: int,
    ) -> None:
        self.pool = pool
        self.sequence_id = sequence_id
        self.max_model_len = max_model_len
        self.max_pages = (max_model_len + pool.page_size - 1) // pool.page_size
        self.block_table = torch.full(
            (self.max_pages,),
            -1,
            device=pool.keys.device,
            dtype=torch.int32,
        )
        self._pages: list[int] = []
        self.length = 0
        self._closed = False

    @property
    def allocated_pages(self) -> tuple[int, ...]:
        return tuple(self._pages)

    def reserve(self, end: int) -> None:
        if self._closed:
            raise RuntimeError("cannot reserve pages for a closed ExactMap sequence")
        if not 0 <= end <= self.max_model_len:
            raise ValueError("KV reservation exceeds the sequence maximum")
        required_pages = (end + self.pool.page_size - 1) // self.pool.page_size
        missing = required_pages - len(self._pages)
        if missing <= 0:
            return
        pages = self.pool.acquire(self.sequence_id, missing)
        start = len(self._pages)
        try:
            self.block_table[start : start + missing].copy_(
                torch.tensor(pages, device=self.block_table.device, dtype=torch.int32)
            )
        except Exception:
            self.pool.release(self.sequence_id, pages)
            raise
        self._pages.extend(pages)

    def reset(self) -> None:
        if self._closed:
            raise RuntimeError("cannot reset a closed ExactMap sequence")
        if self._pages:
            self.pool.release(self.sequence_id, tuple(self._pages))
            self._pages.clear()
        self.block_table.fill_(-1)
        self.length = 0

    def write(
        self,
        layer: int,
        start: int,
        keys: torch.Tensor,
        values: torch.Tensor,
    ) -> None:
        if self._closed:
            raise RuntimeError("cannot write a closed ExactMap sequence")
        if keys.shape != values.shape or keys.ndim != 3:
            raise ValueError("KV update must have matching [token, head, dim] tensors")
        end = start + keys.shape[0]
        expected_tail = self.pool.keys.shape[3:]
        if keys.shape[1:] != expected_tail or end > self.max_model_len:
            raise ValueError("KV update does not fit the ExactMap cache")
        if not 0 <= layer < self.pool.shape.layer_count:
            raise ValueError("KV update has an invalid layer index")
        self.reserve(end)
        positions = torch.arange(start, end, device=self.block_table.device)
        physical_pages = self.block_table[positions // self.pool.page_size].long()
        page_offsets = positions % self.pool.page_size
        self.pool.keys[layer, physical_pages, page_offsets] = keys
        self.pool.values[layer, physical_pages, page_offsets] = values

    def close(self) -> None:
        if self._closed:
            return
        if self._pages:
            self.pool.release(self.sequence_id, tuple(self._pages))
            self._pages.clear()
        self.block_table.fill_(-1)
        self.length = 0
        self._closed = True


class ExactMapQwen3:
    """Explicit Qwen3 forward path specialized for one L40S."""

    def __init__(
        self,
        shape: Qwen3Shape,
        weights: ExactMapWeights,
        *,
        max_model_len: int,
        kv_block_size: int = 16,
        max_num_batched_tokens: int | None = None,
        device: torch.device,
        dtype: torch.dtype,
    ) -> None:
        if len(weights.layers) != shape.layer_count:
            raise ValueError("weight layer count does not match the Qwen shape")
        self.shape = shape
        self.weights = weights
        self.device = device
        self.dtype = dtype
        token_capacity = max_model_len if max_num_batched_tokens is None else max_num_batched_tokens
        self.page_pool = KVPagePool(
            shape,
            token_capacity,
            kv_block_size,
            device=device,
            dtype=dtype,
        )
        self.cache = self.page_pool.new_sequence(max_model_len)
        positions = torch.arange(max_model_len, device=device, dtype=torch.float32)
        frequencies = 1.0 / (
            shape.rope_theta
            ** (
                torch.arange(0, shape.head_dim, 2, device=device, dtype=torch.float32)
                / shape.head_dim
            )
        )
        phase = torch.outer(positions, frequencies)
        embedding = torch.cat((phase, phase), dim=-1)
        self._rope_cos = embedding.cos().to(dtype)
        self._rope_sin = embedding.sin().to(dtype)

    @classmethod
    def from_pretrained(
        cls,
        model_path: str,
        *,
        max_model_len: int,
        kv_block_size: int = 16,
        max_num_batched_tokens: int | None = None,
        device: torch.device,
        dtype: torch.dtype,
    ) -> ExactMapQwen3:
        store = SnapshotWeightStore(model_path, MODEL_REVISION)
        if store.shape != FROZEN_QWEN3_8B_SHAPE:
            raise ValueError(
                "resolved model configuration does not match the frozen Qwen3-8B shape"
            )
        store.verify_weight_digest(MODEL_WEIGHT_DIGEST)
        weights = store.load(device=device, dtype=dtype)
        return cls(
            store.shape,
            weights,
            max_model_len=max_model_len,
            kv_block_size=kv_block_size,
            max_num_batched_tokens=max_num_batched_tokens,
            device=device,
            dtype=dtype,
        )

    @staticmethod
    def _rotate_half(value: torch.Tensor) -> torch.Tensor:
        first, second = value.chunk(2, dim=-1)
        return torch.cat((-second, first), dim=-1)

    def _apply_rope(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        positions: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        cos = self._rope_cos[positions][None, None, :, :]
        sin = self._rope_sin[positions][None, None, :, :]
        return (
            query * cos + self._rotate_half(query) * sin,
            key * cos + self._rotate_half(key) * sin,
        )

    def _project_qkv(
        self,
        hidden: torch.Tensor,
        weights: AttentionWeights,
        positions: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        batch, tokens, _ = hidden.shape
        query = functional.linear(hidden, weights.q_proj).view(
            batch, tokens, self.shape.attention_heads, self.shape.head_dim
        )
        key = functional.linear(hidden, weights.k_proj).view(
            batch, tokens, self.shape.kv_heads, self.shape.head_dim
        )
        value = functional.linear(hidden, weights.v_proj).view(
            batch, tokens, self.shape.kv_heads, self.shape.head_dim
        )
        query = rms_norm(query.contiguous(), weights.q_norm, self.shape.rms_norm_eps)
        key = rms_norm(key.contiguous(), weights.k_norm, self.shape.rms_norm_eps)
        query = query.transpose(1, 2).contiguous()
        key = key.transpose(1, 2).contiguous()
        value = value.transpose(1, 2).contiguous()
        query, key = self._apply_rope(query, key, positions)
        return query, key, value

    def _project_qkv_decode_batch(
        self,
        hidden: torch.Tensor,
        weights: AttentionWeights,
        positions: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        batch = hidden.shape[0]
        query = functional.linear(hidden, weights.q_proj).view(
            batch, 1, self.shape.attention_heads, self.shape.head_dim
        )
        key = functional.linear(hidden, weights.k_proj).view(
            batch, 1, self.shape.kv_heads, self.shape.head_dim
        )
        value = functional.linear(hidden, weights.v_proj).view(
            batch, 1, self.shape.kv_heads, self.shape.head_dim
        )
        query = rms_norm(query.contiguous(), weights.q_norm, self.shape.rms_norm_eps)
        key = rms_norm(key.contiguous(), weights.k_norm, self.shape.rms_norm_eps)
        query = query.transpose(1, 2).contiguous()
        key = key.transpose(1, 2).contiguous()
        value = value.transpose(1, 2).contiguous()
        cos = self._rope_cos[positions][:, None, None, :]
        sin = self._rope_sin[positions][:, None, None, :]
        return (
            query * cos + self._rotate_half(query) * sin,
            key * cos + self._rotate_half(key) * sin,
            value,
        )

    def _prefill_attention(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
    ) -> torch.Tensor:
        try:
            return functional.scaled_dot_product_attention(
                query,
                key,
                value,
                is_causal=True,
                enable_gqa=True,
            )
        except TypeError:  # pragma: no cover - compatibility for older torch.
            groups = self.shape.attention_heads // self.shape.kv_heads
            return functional.scaled_dot_product_attention(
                query,
                key.repeat_interleave(groups, dim=1),
                value.repeat_interleave(groups, dim=1),
                is_causal=True,
            )

    def _mlp(self, hidden: torch.Tensor, weights: MLPWeights) -> torch.Tensor:
        gate = functional.linear(hidden, weights.gate_proj)
        up = functional.linear(hidden, weights.up_proj)
        return functional.linear(silu_mul(gate.contiguous(), up.contiguous()), weights.down_proj)

    def new_cache(self, max_model_len: int | None = None) -> PagedKVCache:
        sequence_maximum = self.cache.max_model_len if max_model_len is None else max_model_len
        return self.page_pool.new_sequence(sequence_maximum)

    def prefill(self, input_ids: torch.Tensor) -> torch.Tensor:
        return self.prefill_sequence(input_ids, self.cache)

    def prefill_sequence(
        self,
        input_ids: torch.Tensor,
        cache: PagedKVCache,
    ) -> torch.Tensor:
        if input_ids.ndim != 2 or input_ids.shape[0] != 1:
            raise ValueError("ExactMap v0.1 prefill accepts exactly one sequence")
        if cache.pool is not self.page_pool:
            raise ValueError("prefill cache does not belong to this ExactMap model")
        token_count = input_ids.shape[1]
        if not 1 <= token_count <= cache.max_model_len:
            raise ValueError("prefill length is outside the ExactMap cache")
        cache.reset()
        cache.reserve(token_count)
        hidden = functional.embedding(input_ids, self.weights.embedding)
        positions = torch.arange(token_count, device=self.device)

        for index, layer in enumerate(self.weights.layers):
            normalized = rms_norm(
                hidden.contiguous(),
                layer.input_norm,
                self.shape.rms_norm_eps,
            )
            query, key, value = self._project_qkv(
                normalized,
                layer.attention,
                positions,
            )
            cache.write(
                index,
                0,
                key[0].transpose(0, 1).contiguous(),
                value[0].transpose(0, 1).contiguous(),
            )
            attention = self._prefill_attention(query, key, value)
            attention = attention.transpose(1, 2).reshape(1, token_count, self.shape.hidden_size)
            attention_update = functional.linear(attention, layer.attention.o_proj)
            residual, mlp_input = add_rms_norm(
                hidden.contiguous(),
                attention_update.contiguous(),
                layer.post_attention_norm,
                self.shape.rms_norm_eps,
            )
            hidden = residual + self._mlp(mlp_input, layer.mlp)

        cache.length = token_count
        normalized = rms_norm(
            hidden[:, -1:, :].contiguous(),
            self.weights.final_norm,
            self.shape.rms_norm_eps,
        )
        return functional.linear(normalized, self.weights.lm_head)[0, 0]

    def decode(self, token_id: int) -> torch.Tensor:
        return self.decode_batch(
            torch.tensor([token_id], device=self.device, dtype=torch.long),
            (self.cache,),
        )[0]

    def decode_batch(
        self,
        token_ids: torch.Tensor,
        caches: tuple[PagedKVCache, ...],
    ) -> torch.Tensor:
        if token_ids.ndim != 1 or token_ids.shape[0] != len(caches) or not caches:
            raise ValueError("decode batch requires one token for every non-empty cache entry")
        if token_ids.device != self.device or token_ids.dtype != torch.long:
            raise ValueError("decode tokens must be long tensors on the ExactMap device")
        if any(cache.pool is not self.page_pool for cache in caches):
            raise ValueError("decode cache does not belong to this ExactMap model")
        if len({cache.sequence_id for cache in caches}) != len(caches):
            raise ValueError("decode batch cannot contain a sequence more than once")
        positions_list = [cache.length for cache in caches]
        if any(
            position < 1 or position >= cache.max_model_len
            for position, cache in zip(positions_list, caches, strict=True)
        ):
            raise ValueError("decode position is outside an ExactMap sequence cache")
        for position, cache in zip(positions_list, caches, strict=True):
            cache.reserve(position + 1)
        positions = torch.tensor(positions_list, device=self.device, dtype=torch.long)
        context_lengths = (positions + 1).to(torch.int32)
        block_tables = torch.stack([cache.block_table for cache in caches])
        hidden = functional.embedding(token_ids[:, None], self.weights.embedding)
        max_context_length = max(positions_list) + 1

        for index, layer in enumerate(self.weights.layers):
            normalized = rms_norm(
                hidden.contiguous(),
                layer.input_norm,
                self.shape.rms_norm_eps,
            )
            query, key, value = self._project_qkv_decode_batch(
                normalized,
                layer.attention,
                positions,
            )
            for batch_index, (position, cache) in enumerate(
                zip(positions_list, caches, strict=True)
            ):
                cache.write(
                    index,
                    position,
                    key[batch_index].transpose(0, 1).contiguous(),
                    value[batch_index].transpose(0, 1).contiguous(),
                )
            attention = decode_paged_gqa_batch(
                query[:, :, 0].contiguous(),
                self.page_pool.keys[index],
                self.page_pool.values[index],
                block_tables,
                context_lengths,
                max_context_length=max_context_length,
            ).reshape(len(caches), 1, self.shape.hidden_size)
            attention_update = functional.linear(attention, layer.attention.o_proj)
            residual, mlp_input = add_rms_norm(
                hidden.contiguous(),
                attention_update.contiguous(),
                layer.post_attention_norm,
                self.shape.rms_norm_eps,
            )
            hidden = residual + self._mlp(mlp_input, layer.mlp)

        for position, cache in zip(positions_list, caches, strict=True):
            cache.length = position + 1
        normalized = rms_norm(
            hidden,
            self.weights.final_norm,
            self.shape.rms_norm_eps,
        )
        return functional.linear(normalized, self.weights.lm_head)[:, 0]
