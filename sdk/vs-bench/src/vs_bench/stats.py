"""Percentile and summary statistics for benchmark results."""

from __future__ import annotations

import math


def percentile(sorted_vals: list[float], p: float) -> float:
    """Return the p-th percentile (0-100) using linear interpolation.

    Values must be pre-sorted in ascending order.
    """
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def pct_block(
    values: list[float],
    multiplier: float = 1.0,
) -> dict[str, float] | None:
    """Compute mean and percentiles (p50/p90/p95/p99) for a list of values.

    Returns None if the list is empty. Applies ``multiplier`` to all output
    values (e.g. pass 1000.0 to convert seconds to milliseconds).
    """
    if not values:
        return None
    s = sorted(values)
    return {
        "mean": sum(s) / len(s) * multiplier,
        "p50": percentile(s, 50) * multiplier,
        "p90": percentile(s, 90) * multiplier,
        "p95": percentile(s, 95) * multiplier,
        "p99": percentile(s, 99) * multiplier,
    }
