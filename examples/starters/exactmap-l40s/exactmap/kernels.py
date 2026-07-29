from __future__ import annotations

import math
from typing import Any

import torch

try:
    import triton
    import triton.language as tl
except ImportError:  # pragma: no cover - exercised on non-CUDA development hosts.
    triton = None
    tl = None


def _torch_rms_norm(x: torch.Tensor, weight: torch.Tensor, eps: float) -> torch.Tensor:
    variance = x.float().square().mean(dim=-1, keepdim=True)
    normalized = x.float() * torch.rsqrt(variance + eps)
    return (normalized * weight.float()).to(x.dtype)


if triton is not None and tl is not None:

    @triton.jit
    def _rms_norm_kernel(
        x_ptr: Any,
        weight_ptr: Any,
        output_ptr: Any,
        row_stride: Any,
        width: tl.constexpr,
        eps: tl.constexpr,
        block_size: tl.constexpr,
    ) -> None:
        row = tl.program_id(0)
        offsets = tl.arange(0, block_size)
        mask = offsets < width
        values = tl.load(x_ptr + row * row_stride + offsets, mask=mask, other=0.0).to(tl.float32)
        weight = tl.load(weight_ptr + offsets, mask=mask, other=0.0).to(tl.float32)
        variance = tl.sum(values * values, axis=0) / width
        normalized = values * tl.rsqrt(variance + eps) * weight
        tl.store(output_ptr + row * row_stride + offsets, normalized, mask=mask)

    @triton.jit
    def _add_rms_norm_kernel(
        residual_ptr: Any,
        update_ptr: Any,
        weight_ptr: Any,
        output_residual_ptr: Any,
        output_normalized_ptr: Any,
        row_stride: Any,
        width: tl.constexpr,
        eps: tl.constexpr,
        block_size: tl.constexpr,
    ) -> None:
        row = tl.program_id(0)
        offsets = tl.arange(0, block_size)
        mask = offsets < width
        residual = tl.load(residual_ptr + row * row_stride + offsets, mask=mask, other=0.0).to(
            tl.float32
        )
        update = tl.load(update_ptr + row * row_stride + offsets, mask=mask, other=0.0).to(
            tl.float32
        )
        combined = (residual + update).to(tl.bfloat16).to(tl.float32)
        weight = tl.load(weight_ptr + offsets, mask=mask, other=0.0).to(tl.float32)
        variance = tl.sum(combined * combined, axis=0) / width
        normalized = combined * tl.rsqrt(variance + eps) * weight
        tl.store(output_residual_ptr + row * row_stride + offsets, combined, mask=mask)
        tl.store(output_normalized_ptr + row * row_stride + offsets, normalized, mask=mask)

    @triton.jit
    def _silu_mul_kernel(
        gate_ptr: Any,
        up_ptr: Any,
        output_ptr: Any,
        element_count: Any,
        block_size: tl.constexpr,
    ) -> None:
        offsets = tl.program_id(0) * block_size + tl.arange(0, block_size)
        mask = offsets < element_count
        gate = tl.load(gate_ptr + offsets, mask=mask).to(tl.float32)
        up = tl.load(up_ptr + offsets, mask=mask).to(tl.float32)
        activated = (gate * tl.sigmoid(gate)).to(tl.bfloat16).to(tl.float32)
        tl.store(output_ptr + offsets, activated * up, mask=mask)

    @triton.jit
    def _decode_gqa_kernel(
        query_ptr: Any,
        key_ptr: Any,
        value_ptr: Any,
        output_ptr: Any,
        context_length: Any,
        query_head_stride: Any,
        cache_token_stride: Any,
        cache_head_stride: Any,
        output_head_stride: Any,
        scale: tl.constexpr,
        group_size: tl.constexpr,
        head_dim: tl.constexpr,
        max_context: tl.constexpr,
        block_tokens: tl.constexpr,
    ) -> None:
        query_head = tl.program_id(0)
        kv_head = query_head // group_size
        dimensions = tl.arange(0, head_dim)
        query = tl.load(query_ptr + query_head * query_head_stride + dimensions).to(tl.float32)

        accumulator = tl.zeros((head_dim,), dtype=tl.float32)
        running_max = -float("inf")
        running_sum = 0.0

        for token_start in tl.range(0, max_context, block_tokens):
            tokens = token_start + tl.arange(0, block_tokens)
            token_mask = tokens < context_length
            key_offsets = (
                tokens[:, None] * cache_token_stride
                + kv_head * cache_head_stride
                + dimensions[None, :]
            )
            keys = tl.load(key_ptr + key_offsets, mask=token_mask[:, None], other=0.0).to(
                tl.float32
            )
            scores = tl.sum(keys * query[None, :], axis=1) * scale
            scores = tl.where(token_mask, scores, -float("inf"))

            block_max = tl.max(scores, axis=0)
            next_max = tl.maximum(running_max, block_max)
            old_scale = tl.exp2((running_max - next_max) * 1.4426950408889634)
            probabilities = tl.exp2((scores - next_max) * 1.4426950408889634)
            probabilities = tl.where(token_mask, probabilities, 0.0)

            value_offsets = (
                tokens[:, None] * cache_token_stride
                + kv_head * cache_head_stride
                + dimensions[None, :]
            )
            values = tl.load(value_ptr + value_offsets, mask=token_mask[:, None], other=0.0).to(
                tl.float32
            )
            accumulator = accumulator * old_scale + tl.sum(probabilities[:, None] * values, axis=0)
            running_sum = running_sum * old_scale + tl.sum(probabilities, axis=0)
            running_max = next_max

        output = accumulator / running_sum
        tl.store(output_ptr + query_head * output_head_stride + dimensions, output)

    @triton.jit
    def _decode_paged_gqa_kernel(
        query_ptr: Any,
        key_ptr: Any,
        value_ptr: Any,
        block_table_ptr: Any,
        output_ptr: Any,
        context_length: Any,
        query_head_stride: Any,
        cache_block_stride: Any,
        cache_token_stride: Any,
        cache_head_stride: Any,
        output_head_stride: Any,
        scale: tl.constexpr,
        group_size: tl.constexpr,
        head_dim: tl.constexpr,
        page_size: tl.constexpr,
        max_context: tl.constexpr,
        block_tokens: tl.constexpr,
    ) -> None:
        query_head = tl.program_id(0)
        kv_head = query_head // group_size
        dimensions = tl.arange(0, head_dim)
        query = tl.load(query_ptr + query_head * query_head_stride + dimensions).to(tl.float32)

        accumulator = tl.zeros((head_dim,), dtype=tl.float32)
        running_max = -float("inf")
        running_sum = 0.0

        for token_start in tl.range(0, max_context, block_tokens):
            tokens = token_start + tl.arange(0, block_tokens)
            token_mask = tokens < context_length
            logical_pages = tokens // page_size
            page_offsets = tokens % page_size
            physical_pages = tl.load(block_table_ptr + logical_pages, mask=token_mask, other=0)
            key_offsets = (
                physical_pages[:, None] * cache_block_stride
                + page_offsets[:, None] * cache_token_stride
                + kv_head * cache_head_stride
                + dimensions[None, :]
            )
            keys = tl.load(key_ptr + key_offsets, mask=token_mask[:, None], other=0.0).to(
                tl.float32
            )
            scores = tl.sum(keys * query[None, :], axis=1) * scale
            scores = tl.where(token_mask, scores, -float("inf"))

            block_max = tl.max(scores, axis=0)
            next_max = tl.maximum(running_max, block_max)
            old_scale = tl.exp2((running_max - next_max) * 1.4426950408889634)
            probabilities = tl.exp2((scores - next_max) * 1.4426950408889634)
            probabilities = tl.where(token_mask, probabilities, 0.0)

            value_offsets = (
                physical_pages[:, None] * cache_block_stride
                + page_offsets[:, None] * cache_token_stride
                + kv_head * cache_head_stride
                + dimensions[None, :]
            )
            values = tl.load(value_ptr + value_offsets, mask=token_mask[:, None], other=0.0).to(
                tl.float32
            )
            accumulator = accumulator * old_scale + tl.sum(probabilities[:, None] * values, axis=0)
            running_sum = running_sum * old_scale + tl.sum(probabilities, axis=0)
            running_max = next_max

        output = accumulator / running_sum
        tl.store(output_ptr + query_head * output_head_stride + dimensions, output)

    @triton.jit
    def _decode_paged_gqa_batch_kernel(
        query_ptr: Any,
        key_ptr: Any,
        value_ptr: Any,
        block_tables_ptr: Any,
        context_lengths_ptr: Any,
        output_ptr: Any,
        query_batch_stride: Any,
        query_head_stride: Any,
        cache_block_stride: Any,
        cache_token_stride: Any,
        cache_head_stride: Any,
        table_batch_stride: Any,
        output_batch_stride: Any,
        output_head_stride: Any,
        scale: tl.constexpr,
        group_size: tl.constexpr,
        head_dim: tl.constexpr,
        page_size: tl.constexpr,
        max_context: tl.constexpr,
        block_tokens: tl.constexpr,
    ) -> None:
        batch = tl.program_id(0)
        query_head = tl.program_id(1)
        kv_head = query_head // group_size
        context_length = tl.load(context_lengths_ptr + batch)
        dimensions = tl.arange(0, head_dim)
        query = tl.load(
            query_ptr + batch * query_batch_stride + query_head * query_head_stride + dimensions
        ).to(tl.float32)

        accumulator = tl.zeros((head_dim,), dtype=tl.float32)
        running_max = -float("inf")
        running_sum = 0.0

        for token_start in tl.range(0, max_context, block_tokens):
            tokens = token_start + tl.arange(0, block_tokens)
            token_mask = tokens < context_length
            logical_pages = tokens // page_size
            page_offsets = tokens % page_size
            physical_pages = tl.load(
                block_tables_ptr + batch * table_batch_stride + logical_pages,
                mask=token_mask,
                other=0,
            )
            key_offsets = (
                physical_pages[:, None] * cache_block_stride
                + page_offsets[:, None] * cache_token_stride
                + kv_head * cache_head_stride
                + dimensions[None, :]
            )
            keys = tl.load(key_ptr + key_offsets, mask=token_mask[:, None], other=0.0).to(
                tl.float32
            )
            scores = tl.sum(keys * query[None, :], axis=1) * scale
            scores = tl.where(token_mask, scores, -float("inf"))

            block_max = tl.max(scores, axis=0)
            next_max = tl.maximum(running_max, block_max)
            old_scale = tl.exp2((running_max - next_max) * 1.4426950408889634)
            probabilities = tl.exp2((scores - next_max) * 1.4426950408889634)
            probabilities = tl.where(token_mask, probabilities, 0.0)

            value_offsets = (
                physical_pages[:, None] * cache_block_stride
                + page_offsets[:, None] * cache_token_stride
                + kv_head * cache_head_stride
                + dimensions[None, :]
            )
            values = tl.load(value_ptr + value_offsets, mask=token_mask[:, None], other=0.0).to(
                tl.float32
            )
            accumulator = accumulator * old_scale + tl.sum(probabilities[:, None] * values, axis=0)
            running_sum = running_sum * old_scale + tl.sum(probabilities, axis=0)
            running_max = next_max

        output = accumulator / running_sum
        tl.store(
            output_ptr + batch * output_batch_stride + query_head * output_head_stride + dimensions,
            output,
        )


def rms_norm(x: torch.Tensor, weight: torch.Tensor, eps: float) -> torch.Tensor:
    """RMSNorm with an L40S Triton path and a numerically matched torch fallback."""

    if not x.is_cuda or triton is None:
        return _torch_rms_norm(x, weight, eps)
    if not x.is_contiguous() or not weight.is_contiguous():
        raise ValueError("ExactMap RMSNorm requires contiguous tensors")
    width = x.shape[-1]
    if width > 65_536:
        raise ValueError(f"RMSNorm width {width} exceeds the ExactMap kernel bound")
    rows = x.numel() // width
    output = torch.empty_like(x)
    block_size = triton.next_power_of_2(width)
    _rms_norm_kernel[(rows,)](
        x,
        weight,
        output,
        width,
        width=width,
        eps=eps,
        block_size=block_size,
        num_warps=8,
    )
    return output


def add_rms_norm(
    residual: torch.Tensor,
    update: torch.Tensor,
    weight: torch.Tensor,
    eps: float,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Fuse the residual add and the following RMSNorm."""

    if residual.shape != update.shape:
        raise ValueError("residual and update tensors must have the same shape")
    if not residual.is_cuda or triton is None:
        combined = residual + update
        return combined, _torch_rms_norm(combined, weight, eps)
    if not residual.is_contiguous() or not update.is_contiguous():
        raise ValueError("ExactMap fused add RMSNorm requires contiguous tensors")
    width = residual.shape[-1]
    rows = residual.numel() // width
    output_residual = torch.empty_like(residual)
    output_normalized = torch.empty_like(residual)
    block_size = triton.next_power_of_2(width)
    _add_rms_norm_kernel[(rows,)](
        residual,
        update,
        weight,
        output_residual,
        output_normalized,
        width,
        width=width,
        eps=eps,
        block_size=block_size,
        num_warps=8,
    )
    return output_residual, output_normalized


def silu_mul(gate: torch.Tensor, up: torch.Tensor) -> torch.Tensor:
    """Fuse SiLU activation with the gated MLP multiply."""

    if gate.shape != up.shape:
        raise ValueError("gate and up tensors must have the same shape")
    if not gate.is_cuda or triton is None:
        return torch.nn.functional.silu(gate.float()).to(gate.dtype) * up
    if not gate.is_contiguous() or not up.is_contiguous():
        raise ValueError("ExactMap fused SiLU multiply requires contiguous tensors")
    output = torch.empty_like(gate)
    element_count = gate.numel()
    block_size = 256
    _silu_mul_kernel[(triton.cdiv(element_count, block_size),)](
        gate,
        up,
        output,
        element_count,
        block_size=block_size,
        num_warps=4,
    )
    return output


def decode_gqa(
    query: torch.Tensor,
    keys: torch.Tensor,
    values: torch.Tensor,
    context_length: int,
) -> torch.Tensor:
    """Single-token grouped-query attention over the realized KV prefix.

    ``query`` has shape ``[query_heads, head_dim]`` and the cache tensors have
    shape ``[max_tokens, kv_heads, head_dim]``. The Triton kernel uses online
    softmax so it never materializes the attention matrix.
    """

    if query.ndim != 2 or keys.ndim != 3 or values.shape != keys.shape:
        raise ValueError("invalid ExactMap decode attention tensor shape")
    query_heads, head_dim = query.shape
    max_tokens, kv_heads, cache_head_dim = keys.shape
    if head_dim != cache_head_dim or query_heads % kv_heads:
        raise ValueError("query and KV head shapes are incompatible")
    if not 1 <= context_length <= max_tokens:
        raise ValueError("context_length is outside the allocated KV cache")

    group_size = query_heads // kv_heads
    scale = 1.0 / math.sqrt(head_dim)
    if not query.is_cuda or triton is None:
        expanded_keys = keys[:context_length].permute(1, 0, 2).repeat_interleave(group_size, dim=0)
        expanded_values = (
            values[:context_length].permute(1, 0, 2).repeat_interleave(group_size, dim=0)
        )
        scores = torch.einsum("hd,htd->ht", query.float(), expanded_keys.float()) * scale
        probabilities = torch.softmax(scores, dim=-1).to(expanded_values.dtype)
        return torch.einsum("ht,htd->hd", probabilities, expanded_values)

    if head_dim != 128:
        raise ValueError("the L40S decode kernel is specialized for head_dim=128")
    if not query.is_contiguous() or not keys.is_contiguous() or not values.is_contiguous():
        raise ValueError("ExactMap decode attention requires contiguous tensors")
    output = torch.empty_like(query)
    max_context = max(64, triton.next_power_of_2(context_length))
    _decode_gqa_kernel[(query_heads,)](
        query,
        keys,
        values,
        output,
        context_length,
        query.stride(0),
        keys.stride(0),
        keys.stride(1),
        output.stride(0),
        scale=scale,
        group_size=group_size,
        head_dim=head_dim,
        max_context=max_context,
        block_tokens=64,
        num_warps=8,
    )
    return output


def decode_paged_gqa(
    query: torch.Tensor,
    keys: torch.Tensor,
    values: torch.Tensor,
    block_table: torch.Tensor,
    context_length: int,
) -> torch.Tensor:
    """Single-token grouped-query attention over a page-table-addressed KV prefix.

    ``keys`` and ``values`` have shape
    ``[physical_page, token_in_page, kv_head, head_dim]``. ``block_table`` maps
    logical pages for one sequence to physical pages in that pool.
    """

    if query.ndim != 2 or keys.ndim != 4 or values.shape != keys.shape:
        raise ValueError("invalid ExactMap paged decode attention tensor shape")
    if block_table.ndim != 1 or block_table.dtype not in (torch.int32, torch.int64):
        raise ValueError("ExactMap block table must be a one-dimensional integer tensor")
    query_heads, head_dim = query.shape
    page_count, page_size, kv_heads, cache_head_dim = keys.shape
    if head_dim != cache_head_dim or query_heads % kv_heads:
        raise ValueError("query and paged KV head shapes are incompatible")
    if not (query.device == keys.device == values.device == block_table.device):
        raise ValueError("query, paged KV, and block table must use the same device")
    required_pages = (context_length + page_size - 1) // page_size
    if not 1 <= context_length <= block_table.numel() * page_size:
        raise ValueError("context_length is outside the ExactMap block table")
    realized_table = block_table[:required_pages]

    group_size = query_heads // kv_heads
    scale = 1.0 / math.sqrt(head_dim)
    if not query.is_cuda or triton is None:
        if bool(torch.any(realized_table < 0).item()) or bool(
            torch.any(realized_table >= page_count).item()
        ):
            raise ValueError("ExactMap block table contains an unallocated page")
        positions = torch.arange(context_length, device=keys.device)
        physical_pages = realized_table[positions // page_size].long()
        offsets = positions % page_size
        realized_keys = keys[physical_pages, offsets]
        realized_values = values[physical_pages, offsets]
        expanded_keys = realized_keys.permute(1, 0, 2).repeat_interleave(group_size, dim=0)
        expanded_values = realized_values.permute(1, 0, 2).repeat_interleave(group_size, dim=0)
        scores = torch.einsum("hd,htd->ht", query.float(), expanded_keys.float()) * scale
        probabilities = torch.softmax(scores, dim=-1).to(expanded_values.dtype)
        return torch.einsum("ht,htd->hd", probabilities, expanded_values)

    if head_dim != 128 or page_size != 16:
        raise ValueError("the L40S paged decode kernel requires head_dim=128 and page_size=16")
    if (
        not query.is_contiguous()
        or not keys.is_contiguous()
        or not values.is_contiguous()
        or not block_table.is_contiguous()
    ):
        raise ValueError("ExactMap paged decode attention requires contiguous tensors")
    output = torch.empty_like(query)
    max_context = max(64, triton.next_power_of_2(context_length))
    _decode_paged_gqa_kernel[(query_heads,)](
        query,
        keys,
        values,
        block_table,
        output,
        context_length,
        query.stride(0),
        keys.stride(0),
        keys.stride(1),
        keys.stride(2),
        output.stride(0),
        scale=scale,
        group_size=group_size,
        head_dim=head_dim,
        page_size=page_size,
        max_context=max_context,
        block_tokens=64,
        num_warps=8,
    )
    return output


def decode_paged_gqa_batch(
    query: torch.Tensor,
    keys: torch.Tensor,
    values: torch.Tensor,
    block_tables: torch.Tensor,
    context_lengths: torch.Tensor,
    *,
    max_context_length: int | None = None,
) -> torch.Tensor:
    """Batched page-table-aware GQA for one decode token per active sequence."""

    if query.ndim != 3 or keys.ndim != 4 or values.shape != keys.shape:
        raise ValueError("invalid ExactMap batched paged attention tensor shape")
    if block_tables.ndim != 2 or block_tables.dtype not in (torch.int32, torch.int64):
        raise ValueError("ExactMap batched block tables must be a two-dimensional integer tensor")
    if context_lengths.ndim != 1 or context_lengths.dtype not in (torch.int32, torch.int64):
        raise ValueError("ExactMap context lengths must be a one-dimensional integer tensor")
    batch_size, query_heads, head_dim = query.shape
    page_count, page_size, kv_heads, cache_head_dim = keys.shape
    if (
        block_tables.shape[0] != batch_size
        or context_lengths.shape[0] != batch_size
        or head_dim != cache_head_dim
        or query_heads % kv_heads
    ):
        raise ValueError("batched query, page table, context, and KV shapes are incompatible")
    if not (
        query.device
        == keys.device
        == values.device
        == block_tables.device
        == context_lengths.device
    ):
        raise ValueError("batched query, paged KV, tables, and lengths must use the same device")
    if max_context_length is None:
        max_context_length = int(context_lengths.max().item())
    if not 1 <= max_context_length <= block_tables.shape[1] * page_size:
        raise ValueError("maximum context length is outside the ExactMap block tables")

    if not query.is_cuda or triton is None:
        lengths = [int(value) for value in context_lengths.tolist()]
        if any(length < 1 or length > max_context_length for length in lengths):
            raise ValueError("context length is outside the declared batch maximum")
        return torch.stack(
            [
                decode_paged_gqa(
                    query[index],
                    keys,
                    values,
                    block_tables[index],
                    lengths[index],
                )
                for index in range(batch_size)
            ]
        )

    if head_dim != 128 or page_size != 16:
        raise ValueError(
            "the L40S batched paged decode kernel requires head_dim=128 and page_size=16"
        )
    if (
        not query.is_contiguous()
        or not keys.is_contiguous()
        or not values.is_contiguous()
        or not block_tables.is_contiguous()
        or not context_lengths.is_contiguous()
    ):
        raise ValueError("ExactMap batched paged attention requires contiguous tensors")

    output = torch.empty_like(query)
    compiled_context = max(64, triton.next_power_of_2(max_context_length))
    _decode_paged_gqa_batch_kernel[(batch_size, query_heads)](
        query,
        keys,
        values,
        block_tables,
        context_lengths,
        output,
        query.stride(0),
        query.stride(1),
        keys.stride(0),
        keys.stride(1),
        keys.stride(2),
        block_tables.stride(0),
        output.stride(0),
        output.stride(1),
        scale=1.0 / math.sqrt(head_dim),
        group_size=query_heads // kv_heads,
        head_dim=head_dim,
        page_size=page_size,
        max_context=compiled_context,
        block_tokens=64,
        num_warps=8,
    )
    return output
