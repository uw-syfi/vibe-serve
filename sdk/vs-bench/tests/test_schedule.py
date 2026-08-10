from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from vs_bench.schedule import closed_loop, constant, duration_limited, poisson


async def _collect(ait: AsyncIterator[int], max_items: int = 10000) -> list[int]:
    items: list[int] = []
    async for i in ait:
        items.append(i)
        if len(items) >= max_items:
            break
    return items


class TestClosedLoop:
    @pytest.mark.asyncio
    async def test_yields_n_indices(self) -> None:
        result = await _collect(closed_loop(5))
        assert result == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_zero_yields_nothing(self) -> None:
        result = await _collect(closed_loop(0))
        assert result == []

    @pytest.mark.asyncio
    async def test_no_delay(self) -> None:
        start = time.perf_counter()
        await _collect(closed_loop(100))
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1


class TestConstant:
    @pytest.mark.asyncio
    async def test_yields_correct_count(self) -> None:
        result = await _collect(constant(3, rate=100.0))
        assert result == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_approximate_timing(self) -> None:
        start = time.perf_counter()
        await _collect(constant(3, rate=20.0))
        elapsed = time.perf_counter() - start
        assert 0.08 < elapsed < 0.2


class TestPoisson:
    @pytest.mark.asyncio
    async def test_yields_correct_count(self) -> None:
        result = await _collect(poisson(5, rate=1000.0, seed=1))
        assert result == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_deterministic_with_seed(self) -> None:
        t1 = time.perf_counter()
        await _collect(poisson(10, rate=100.0, seed=99))
        d1 = time.perf_counter() - t1

        t2 = time.perf_counter()
        await _collect(poisson(10, rate=100.0, seed=99))
        d2 = time.perf_counter() - t2

        assert abs(d1 - d2) < 0.05

    @pytest.mark.asyncio
    async def test_total_time_reasonable(self) -> None:
        start = time.perf_counter()
        await _collect(poisson(10, rate=50.0, seed=7))
        elapsed = time.perf_counter() - start
        assert 0.05 < elapsed < 1.0


class TestDurationLimited:
    @pytest.mark.asyncio
    async def test_stops_after_duration(self) -> None:
        start = time.perf_counter()
        result = await _collect(duration_limited(0.1))
        elapsed = time.perf_counter() - start
        assert elapsed < 0.2
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_indices_start_at_zero(self) -> None:
        result = await _collect(duration_limited(0.05))
        assert result[0] == 0
        assert result == list(range(len(result)))

    @pytest.mark.asyncio
    async def test_zero_duration_yields_nothing(self) -> None:
        result = await _collect(duration_limited(0.0))
        assert result == []
