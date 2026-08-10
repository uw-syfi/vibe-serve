from __future__ import annotations

import math

from vs_bench.stats import pct_block, percentile


class TestPercentile:
    def test_empty_returns_nan(self) -> None:
        assert math.isnan(percentile([], 50))

    def test_single_value(self) -> None:
        assert percentile([7.0], 0) == 7.0
        assert percentile([7.0], 50) == 7.0
        assert percentile([7.0], 100) == 7.0

    def test_two_values_median(self) -> None:
        assert percentile([1.0, 3.0], 50) == 2.0

    def test_known_quartiles(self) -> None:
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert percentile(vals, 0) == 1.0
        assert percentile(vals, 25) == 2.0
        assert percentile(vals, 50) == 3.0
        assert percentile(vals, 75) == 4.0
        assert percentile(vals, 100) == 5.0

    def test_interpolation(self) -> None:
        vals = [10.0, 20.0, 30.0, 40.0]
        p33 = percentile(vals, 33)
        assert 19.0 < p33 < 20.0

    def test_p99_with_100_values(self) -> None:
        vals = list(range(100))
        result = percentile([float(v) for v in vals], 99)
        assert result == 98.01


class TestPctBlock:
    def test_empty_returns_none(self) -> None:
        assert pct_block([]) is None

    def test_single_value_all_equal(self) -> None:
        result = pct_block([5.0])
        assert result is not None
        assert result["mean"] == 5.0
        assert result["p50"] == 5.0
        assert result["p90"] == 5.0
        assert result["p95"] == 5.0
        assert result["p99"] == 5.0

    def test_multiplier(self) -> None:
        result = pct_block([1.0, 2.0, 3.0], multiplier=1000.0)
        assert result is not None
        assert result["mean"] == 2000.0
        assert result["p50"] == 2000.0

    def test_ordering_invariance(self) -> None:
        a = pct_block([3.0, 1.0, 2.0])
        b = pct_block([1.0, 2.0, 3.0])
        assert a == b

    def test_keys_present(self) -> None:
        result = pct_block([1.0, 2.0])
        assert result is not None
        assert set(result.keys()) == {"mean", "p50", "p90", "p95", "p99"}
