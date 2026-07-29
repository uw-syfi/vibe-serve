from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest
import torch
from exactmap.kernels import (
    add_rms_norm,
    decode_gqa,
    decode_paged_gqa,
    decode_paged_gqa_batch,
    rms_norm,
    silu_mul,
)
from exactmap.model import (
    ExactMapQwen3,
    ExactMapWeights,
    KVPagePool,
    Qwen3Shape,
    SnapshotWeightStore,
)
from safetensors.torch import save_file
from transformers import Qwen3Config, Qwen3ForCausalLM


def tiny_reference() -> tuple[Qwen3ForCausalLM, ExactMapQwen3]:
    torch.manual_seed(17)
    config = Qwen3Config(
        vocab_size=128,
        hidden_size=64,
        intermediate_size=128,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        head_dim=16,
        max_position_embeddings=64,
        rope_theta=10_000.0,
        rms_norm_eps=1e-6,
        attention_bias=False,
        tie_word_embeddings=False,
    )
    reference = Qwen3ForCausalLM(config).eval()
    shape = Qwen3Shape.from_config(config.to_dict())
    weights = ExactMapWeights.from_state_dict(
        reference.state_dict(),
        shape,
        device=torch.device("cpu"),
        dtype=torch.float32,
    )
    candidate = ExactMapQwen3(
        shape,
        weights,
        max_model_len=64,
        device=torch.device("cpu"),
        dtype=torch.float32,
    )
    return reference, candidate


def test_torch_kernel_fallbacks_match_reference_operations() -> None:
    torch.manual_seed(3)
    x = torch.randn(7, 64)
    update = torch.randn(7, 64)
    weight = torch.randn(64)

    expected_norm = torch.nn.functional.rms_norm(x, (64,), weight, eps=1e-6)
    assert torch.allclose(rms_norm(x, weight, 1e-6), expected_norm, atol=1e-6, rtol=1e-5)

    residual, normalized = add_rms_norm(x, update, weight, 1e-6)
    assert torch.allclose(residual, x + update)
    assert torch.allclose(
        normalized,
        torch.nn.functional.rms_norm(x + update, (64,), weight, eps=1e-6),
        atol=1e-6,
        rtol=1e-5,
    )

    gate = torch.randn(2, 3, 32)
    up = torch.randn(2, 3, 32)
    assert torch.allclose(
        silu_mul(gate, up),
        torch.nn.functional.silu(gate) * up,
        atol=1e-6,
        rtol=1e-5,
    )


def test_decode_gqa_matches_materialized_attention() -> None:
    torch.manual_seed(4)
    query = torch.randn(4, 16)
    keys = torch.randn(23, 2, 16)
    values = torch.randn(23, 2, 16)

    actual = decode_gqa(query, keys, values, context_length=19)
    expanded_keys = keys[:19].permute(1, 0, 2).repeat_interleave(2, dim=0)
    expanded_values = values[:19].permute(1, 0, 2).repeat_interleave(2, dim=0)
    scores = torch.einsum("hd,htd->ht", query, expanded_keys) / math.sqrt(16)
    expected = torch.einsum("ht,htd->hd", torch.softmax(scores, dim=-1), expanded_values)

    assert torch.allclose(actual, expected, atol=1e-5, rtol=1e-5)


def test_paged_decode_gqa_follows_a_fragmented_block_table() -> None:
    torch.manual_seed(8)
    query = torch.randn(4, 16)
    keys = torch.randn(4, 16, 2, 16)
    values = torch.randn_like(keys)
    block_table = torch.tensor([2, 0, -1], dtype=torch.int32)
    context_length = 19

    actual = decode_paged_gqa(
        query,
        keys,
        values,
        block_table,
        context_length,
    )
    logical_keys = torch.cat((keys[2], keys[0]), dim=0)[:context_length]
    logical_values = torch.cat((values[2], values[0]), dim=0)[:context_length]
    expanded_keys = logical_keys.permute(1, 0, 2).repeat_interleave(2, dim=0)
    expanded_values = logical_values.permute(1, 0, 2).repeat_interleave(2, dim=0)
    scores = torch.einsum("hd,htd->ht", query, expanded_keys) / math.sqrt(16)
    expected = torch.einsum("ht,htd->hd", torch.softmax(scores, dim=-1), expanded_values)

    assert torch.allclose(actual, expected, atol=1e-5, rtol=1e-5)


def test_batched_paged_decode_matches_independent_sequence_attention() -> None:
    torch.manual_seed(9)
    query = torch.randn(3, 4, 16)
    keys = torch.randn(8, 16, 2, 16)
    values = torch.randn_like(keys)
    block_tables = torch.tensor(
        [
            [3, 1, -1],
            [5, 0, 7],
            [2, 6, 4],
        ],
        dtype=torch.int32,
    )
    context_lengths = torch.tensor([19, 33, 47], dtype=torch.int32)

    actual = decode_paged_gqa_batch(
        query,
        keys,
        values,
        block_tables,
        context_lengths,
        max_context_length=47,
    )
    expected = torch.stack(
        [
            decode_paged_gqa(
                query[index],
                keys,
                values,
                block_tables[index],
                int(context_lengths[index]),
            )
            for index in range(3)
        ]
    )

    assert torch.allclose(actual, expected, atol=1e-5, rtol=1e-5)


def test_kv_page_pool_allocates_reuses_and_preserves_cross_page_writes() -> None:
    shape = Qwen3Shape(
        hidden_size=16,
        intermediate_size=32,
        layer_count=2,
        attention_heads=2,
        kv_heads=1,
        head_dim=8,
        vocab_size=32,
        rms_norm_eps=1e-6,
        rope_theta=10_000,
    )
    pool = KVPagePool(
        shape,
        token_capacity=64,
        page_size=16,
        device=torch.device("cpu"),
        dtype=torch.float32,
    )
    sequence = pool.new_sequence(max_model_len=48)
    keys = torch.arange(4 * 1 * 8, dtype=torch.float32).reshape(4, 1, 8)
    values = keys + 100

    sequence.write(0, 14, keys, values)

    assert sequence.allocated_pages == (0, 1)
    assert sequence.block_table[:2].tolist() == [0, 1]
    assert pool.allocated_page_count == 2
    assert torch.equal(pool.keys[0, 0, 14:16], keys[:2])
    assert torch.equal(pool.keys[0, 1, :2], keys[2:])
    assert torch.equal(pool.values[0, 1, :2], values[2:])

    sequence.reset()
    assert pool.free_page_count == 4
    replacement = pool.new_sequence(max_model_len=16)
    replacement.reserve(16)
    assert replacement.allocated_pages == (0,)
    replacement.close()
    replacement.close()
    assert pool.free_page_count == 4


def test_kv_page_pool_exhaustion_is_atomic_and_owner_checked() -> None:
    shape = Qwen3Shape(
        hidden_size=16,
        intermediate_size=32,
        layer_count=1,
        attention_heads=2,
        kv_heads=1,
        head_dim=8,
        vocab_size=32,
        rms_norm_eps=1e-6,
        rope_theta=10_000,
    )
    pool = KVPagePool(
        shape,
        token_capacity=32,
        page_size=16,
        device=torch.device("cpu"),
        dtype=torch.float32,
    )
    first = pool.new_sequence(max_model_len=32)
    second = pool.new_sequence(max_model_len=32)
    first.reserve(32)

    with pytest.raises(RuntimeError, match="page pool exhausted"):
        second.reserve(1)

    assert second.allocated_pages == ()
    assert second.block_table.tolist() == [-1, -1]
    assert pool.allocated_page_count == 2
    with pytest.raises(RuntimeError, match="does not match its owner"):
        pool.release(second.sequence_id, first.allocated_pages)


def test_explicit_qwen_prefill_and_decode_match_transformers_oracle() -> None:
    reference, candidate = tiny_reference()
    input_ids = (torch.arange(18, dtype=torch.long) * 7 + 3).remainder(128)[None, :]

    with torch.inference_mode():
        reference_prefill = reference(input_ids, use_cache=True)
        candidate_prefill = candidate.prefill(input_ids)

    assert torch.allclose(
        candidate_prefill,
        reference_prefill.logits[0, -1],
        atol=2e-5,
        rtol=2e-4,
    )
    token_id = int(torch.argmax(reference_prefill.logits[0, -1]).item())

    with torch.inference_mode():
        reference_decode = reference(
            torch.tensor([[token_id]]),
            past_key_values=reference_prefill.past_key_values,
            use_cache=True,
        )
        candidate_decode = candidate.decode(token_id)

    assert candidate.cache.length == input_ids.shape[1] + 1
    assert torch.allclose(
        candidate_decode,
        reference_decode.logits[0, -1],
        atol=2e-5,
        rtol=2e-4,
    )


def test_batched_qwen_decode_matches_independent_transformers_sequences() -> None:
    reference, candidate = tiny_reference()
    first_ids = torch.tensor([[4, 7, 11, 18, 29]], dtype=torch.long)
    second_ids = torch.tensor([[8, 13, 21, 34, 55, 89, 16]], dtype=torch.long)
    first_cache = candidate.new_cache(max_model_len=32)
    second_cache = candidate.new_cache(max_model_len=32)

    with torch.inference_mode():
        first_reference = reference(first_ids, use_cache=True)
        second_reference = reference(second_ids, use_cache=True)
        first_candidate = candidate.prefill_sequence(first_ids, first_cache)
        second_candidate = candidate.prefill_sequence(second_ids, second_cache)

    assert torch.allclose(
        first_candidate,
        first_reference.logits[0, -1],
        atol=2e-5,
        rtol=2e-4,
    )
    assert torch.allclose(
        second_candidate,
        second_reference.logits[0, -1],
        atol=2e-5,
        rtol=2e-4,
    )
    token_ids = torch.tensor(
        [
            int(torch.argmax(first_reference.logits[0, -1]).item()),
            int(torch.argmax(second_reference.logits[0, -1]).item()),
        ],
        dtype=torch.long,
    )

    with torch.inference_mode():
        first_decode = reference(
            token_ids[:1, None],
            past_key_values=first_reference.past_key_values,
            use_cache=True,
        )
        second_decode = reference(
            token_ids[1:, None],
            past_key_values=second_reference.past_key_values,
            use_cache=True,
        )
        candidate_decode = candidate.decode_batch(
            token_ids,
            (first_cache, second_cache),
        )

    assert torch.allclose(
        candidate_decode[0],
        first_decode.logits[0, -1],
        atol=2e-5,
        rtol=2e-4,
    )
    assert torch.allclose(
        candidate_decode[1],
        second_decode.logits[0, -1],
        atol=2e-5,
        rtol=2e-4,
    )
    assert first_cache.length == first_ids.shape[1] + 1
    assert second_cache.length == second_ids.shape[1] + 1


def test_snapshot_store_verifies_the_canonical_weight_manifest(tmp_path: Path) -> None:
    config = Qwen3Config(
        vocab_size=16,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=1,
        num_attention_heads=2,
        num_key_value_heads=1,
        head_dim=8,
    )
    (tmp_path / "config.json").write_text(
        json.dumps(config.to_dict()),
        encoding="utf-8",
    )
    shard = tmp_path / "model.safetensors"
    save_file({"weight": torch.arange(8)}, shard)
    shard_digest = hashlib.sha256(shard.read_bytes()).hexdigest()
    manifest = f"{shard_digest}  model.safetensors\n".encode()
    expected = f"sha256:{hashlib.sha256(manifest).hexdigest()}"
    store = SnapshotWeightStore(str(tmp_path), revision="unused")

    store.verify_weight_digest(expected)
    with pytest.raises(ValueError, match="weight digest mismatch"):
        store.verify_weight_digest("sha256:" + ("0" * 64))
