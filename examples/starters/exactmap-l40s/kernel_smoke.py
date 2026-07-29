from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Callable

import torch
from exactmap.kernels import (
    add_rms_norm,
    decode_gqa,
    decode_paged_gqa,
    decode_paged_gqa_batch,
    rms_norm,
    silu_mul,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile and numerically check the ExactMap Triton kernels on one L40S."
    )
    parser.add_argument("--context-length", type=int, default=1_024)
    parser.add_argument("--iterations", type=int, default=5)
    return parser.parse_args()


def max_abs(left: torch.Tensor, right: torch.Tensor) -> float:
    return float(torch.max(torch.abs(left.float() - right.float())).item())


def elapsed_ms(
    operation: Callable[[], torch.Tensor | tuple[torch.Tensor, ...]], runs: int
) -> float:
    for _ in range(2):
        operation()
    torch.cuda.synchronize()
    started = time.perf_counter()
    for _ in range(runs):
        operation()
    torch.cuda.synchronize()
    return (time.perf_counter() - started) * 1_000 / runs


def main() -> None:
    args = parse_args()
    if args.context_length < 1 or args.context_length > 16_384:
        raise ValueError("context length must be between 1 and 16384")
    if args.iterations < 1:
        raise ValueError("iterations must be positive")
    if not torch.cuda.is_available():
        raise RuntimeError("ExactMap kernel smoke requires CUDA")

    properties = torch.cuda.get_device_properties(0)
    capability = torch.cuda.get_device_capability(0)
    if "L40S" not in properties.name or capability != (8, 9):
        raise RuntimeError("ExactMap kernel smoke requires an NVIDIA L40S")

    torch.manual_seed(17)
    device = torch.device("cuda:0")
    dtype = torch.bfloat16
    hidden = torch.randn((16, 4_096), device=device, dtype=dtype)
    update = torch.randn_like(hidden)
    norm_weight = torch.randn((4_096,), device=device, dtype=dtype)
    gate = torch.randn((16, 12_288), device=device, dtype=dtype)
    up = torch.randn_like(gate)
    query = torch.randn((32, 128), device=device, dtype=dtype)
    keys = torch.randn((args.context_length, 8, 128), device=device, dtype=dtype)
    values = torch.randn_like(keys)
    logical_page_count = (args.context_length + 15) // 16
    paged_keys = torch.randn(
        (logical_page_count + 2, 16, 8, 128),
        device=device,
        dtype=dtype,
    )
    paged_values = torch.randn_like(paged_keys)
    block_table = torch.arange(
        logical_page_count - 1,
        -1,
        -1,
        device=device,
        dtype=torch.int32,
    )
    batched_query = torch.randn((4, 32, 128), device=device, dtype=dtype)
    batched_keys = torch.randn(
        (logical_page_count * 4, 16, 8, 128),
        device=device,
        dtype=dtype,
    )
    batched_values = torch.randn_like(batched_keys)
    batched_tables = (
        torch.arange(
            logical_page_count * 4,
            device=device,
            dtype=torch.int32,
        )
        .reshape(4, logical_page_count)
        .flip(1)
        .contiguous()
    )
    batched_lengths = torch.full(
        (4,),
        args.context_length,
        device=device,
        dtype=torch.int32,
    )

    norm_actual = rms_norm(hidden, norm_weight, 1e-6)
    norm_expected = torch.nn.functional.rms_norm(
        hidden.float(),
        (4_096,),
        norm_weight.float(),
        eps=1e-6,
    ).to(dtype)
    residual_actual, add_norm_actual = add_rms_norm(
        hidden,
        update,
        norm_weight,
        1e-6,
    )
    residual_expected = hidden + update
    add_norm_expected = torch.nn.functional.rms_norm(
        residual_expected.float(),
        (4_096,),
        norm_weight.float(),
        eps=1e-6,
    ).to(dtype)
    silu_actual = silu_mul(gate, up)
    silu_expected = (torch.nn.functional.silu(gate.float()) * up.float()).to(dtype)
    attention_actual = decode_gqa(
        query,
        keys,
        values,
        args.context_length,
    )
    expanded_keys = keys.permute(1, 0, 2).repeat_interleave(4, dim=0)
    expanded_values = values.permute(1, 0, 2).repeat_interleave(4, dim=0)
    scores = torch.einsum("hd,htd->ht", query.float(), expanded_keys.float()) / math.sqrt(128)
    attention_expected = torch.einsum(
        "ht,htd->hd",
        torch.softmax(scores, dim=-1),
        expanded_values.float(),
    ).to(dtype)
    paged_attention_actual = decode_paged_gqa(
        query,
        paged_keys,
        paged_values,
        block_table,
        args.context_length,
    )
    positions = torch.arange(args.context_length, device=device)
    physical_pages = block_table[positions // 16].long()
    page_offsets = positions % 16
    logical_keys = paged_keys[physical_pages, page_offsets]
    logical_values = paged_values[physical_pages, page_offsets]
    expanded_paged_keys = logical_keys.permute(1, 0, 2).repeat_interleave(4, dim=0)
    expanded_paged_values = logical_values.permute(1, 0, 2).repeat_interleave(4, dim=0)
    paged_scores = torch.einsum(
        "hd,htd->ht",
        query.float(),
        expanded_paged_keys.float(),
    ) / math.sqrt(128)
    paged_attention_expected = torch.einsum(
        "ht,htd->hd",
        torch.softmax(paged_scores, dim=-1),
        expanded_paged_values.float(),
    ).to(dtype)
    batched_attention_actual = decode_paged_gqa_batch(
        batched_query,
        batched_keys,
        batched_values,
        batched_tables,
        batched_lengths,
        max_context_length=args.context_length,
    )
    batched_attention_expected = torch.stack(
        [
            decode_paged_gqa(
                batched_query[index],
                batched_keys,
                batched_values,
                batched_tables[index],
                args.context_length,
            )
            for index in range(4)
        ]
    )

    errors = {
        "rmsNorm": max_abs(norm_actual, norm_expected),
        "addRmsNormResidual": max_abs(residual_actual, residual_expected),
        "addRmsNormNormalized": max_abs(add_norm_actual, add_norm_expected),
        "siluMul": max_abs(silu_actual, silu_expected),
        "decodeGqa": max_abs(attention_actual, attention_expected),
        "decodePagedGqa": max_abs(
            paged_attention_actual,
            paged_attention_expected,
        ),
        "decodePagedGqaBatch": max_abs(
            batched_attention_actual,
            batched_attention_expected,
        ),
    }
    limits = {
        "rmsNorm": 0.03125,
        "addRmsNormResidual": 0.03125,
        "addRmsNormNormalized": 0.03125,
        "siluMul": 0.0625,
        "decodeGqa": 0.03125,
        "decodePagedGqa": 0.03125,
        "decodePagedGqaBatch": 0.03125,
    }
    failures = sorted(name for name, error in errors.items() if error > limits[name])
    timings = {
        "rmsNormMs": elapsed_ms(lambda: rms_norm(hidden, norm_weight, 1e-6), args.iterations),
        "addRmsNormMs": elapsed_ms(
            lambda: add_rms_norm(hidden, update, norm_weight, 1e-6),
            args.iterations,
        ),
        "siluMulMs": elapsed_ms(lambda: silu_mul(gate, up), args.iterations),
        "decodeGqaMs": elapsed_ms(
            lambda: decode_gqa(query, keys, values, args.context_length),
            args.iterations,
        ),
        "decodePagedGqaMs": elapsed_ms(
            lambda: decode_paged_gqa(
                query,
                paged_keys,
                paged_values,
                block_table,
                args.context_length,
            ),
            args.iterations,
        ),
        "decodePagedGqaBatchMs": elapsed_ms(
            lambda: decode_paged_gqa_batch(
                batched_query,
                batched_keys,
                batched_values,
                batched_tables,
                batched_lengths,
                max_context_length=args.context_length,
            ),
            args.iterations,
        ),
    }
    result = {
        "schemaVersion": "exactmap.kernel-smoke.v1",
        "claimScope": "none",
        "status": "passed" if not failures else "failed",
        "gpu": {
            "name": properties.name,
            "computeCapability": f"{capability[0]}.{capability[1]}",
            "memoryBytes": properties.total_memory,
        },
        "software": {
            "torchVersion": torch.__version__,
            "cudaVersion": torch.version.cuda,
        },
        "contextLength": args.context_length,
        "errors": errors,
        "limits": limits,
        "timings": timings,
        "failures": failures,
    }
    print(json.dumps(result, sort_keys=True))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
