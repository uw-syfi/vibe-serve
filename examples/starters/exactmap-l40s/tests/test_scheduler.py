from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import pytest
import torch
from exactmap.scheduler import ExactMapScheduler
from exactmap.types import GenerationInput


@dataclass
class FakeCache:
    sequence_id: int
    decode_calls: int = 0
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class FakeTokenizer:
    def decode(self, token_ids: list[int], **_kwargs: Any) -> str:
        return "".join(chr(96 + token_id) for token_id in token_ids)


class FakeBatchModel:
    device = torch.device("cpu")

    def __init__(self, *, fail_decode: bool = False) -> None:
        self.fail_decode = fail_decode
        self.prefill_started = threading.Event()
        self.allow_prefill = threading.Event()
        self.caches: list[FakeCache] = []
        self.decode_batch_sizes: list[int] = []

    def new_cache(self) -> FakeCache:
        cache = FakeCache(sequence_id=len(self.caches))
        self.caches.append(cache)
        return cache

    def prefill_sequence(
        self,
        _input_ids: torch.Tensor,
        _cache: FakeCache,
    ) -> torch.Tensor:
        self.prefill_started.set()
        if not self.allow_prefill.wait(timeout=5):
            raise RuntimeError("test prefill gate timed out")
        logits = torch.full((8,), -10.0)
        logits[1] = 10.0
        return logits

    def decode_batch(
        self,
        _token_ids: torch.Tensor,
        caches: tuple[FakeCache, ...],
    ) -> torch.Tensor:
        if self.fail_decode:
            raise RuntimeError("injected decode failure")
        self.decode_batch_sizes.append(len(caches))
        logits = torch.full((len(caches), 8), -10.0)
        for index, cache in enumerate(caches):
            logits[index, 2 if cache.decode_calls == 0 else 7] = 10.0
            cache.decode_calls += 1
        return logits


def request(*, max_tokens: int = 8) -> GenerationInput:
    return GenerationInput(
        prompt="test",
        messages=(),
        enable_thinking=False,
        min_tokens=0,
        max_tokens=max_tokens,
        temperature=0,
        top_p=1,
        top_k=0,
        seed=17,
    )


def test_scheduler_forms_a_decode_batch_and_releases_every_cache() -> None:
    model = FakeBatchModel()
    scheduler = ExactMapScheduler(  # type: ignore[arg-type]
        model,
        FakeTokenizer(),
        max_batch_size=2,
        eos_ids=(7,),
    )
    try:
        first = scheduler.submit(request(), torch.tensor([[1, 2, 3]]))
        assert model.prefill_started.wait(timeout=5)
        second = scheduler.submit(request(), torch.tensor([[4, 5]]))
        model.allow_prefill.set()

        first_tokens = list(first.tokens)
        second_tokens = list(second.tokens)
    finally:
        model.allow_prefill.set()
        scheduler.close()

    assert [token.token_id for token in first_tokens] == [1, 2]
    assert [token.text for token in first_tokens] == ["a", "b"]
    assert [token.token_id for token in second_tokens] == [1, 2]
    assert 2 in model.decode_batch_sizes
    assert all(cache.closed for cache in model.caches)


def test_scheduler_propagates_batch_failure_and_releases_cache() -> None:
    model = FakeBatchModel(fail_decode=True)
    scheduler = ExactMapScheduler(  # type: ignore[arg-type]
        model,
        FakeTokenizer(),
        max_batch_size=2,
        eos_ids=(7,),
    )
    try:
        session = scheduler.submit(request(), torch.tensor([[1, 2, 3]]))
        assert model.prefill_started.wait(timeout=5)
        model.allow_prefill.set()

        with pytest.raises(RuntimeError, match="scheduled generation failed"):
            list(session.tokens)
    finally:
        model.allow_prefill.set()
        scheduler.close()

    assert len(model.caches) == 1
    assert model.caches[0].closed is True
