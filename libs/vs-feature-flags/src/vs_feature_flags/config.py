from __future__ import annotations  # noqa: D100  # tracked: #288

from collections.abc import Mapping
from enum import StrEnum
from typing import TypeVar

FlagT = TypeVar("FlagT", bound=StrEnum)


def parse_feature_flag_overrides(
    raw: object,
    flag_type: type[FlagT],
    *,
    section_name: str = "feature_flags",
) -> dict[FlagT, bool]:
    """Parse user config into typed feature flag overrides.

    The library accepts only TOML-style boolean values. It intentionally does
    not coerce strings like ``"true"`` so config mistakes fail early.
    """
    if raw is None:
        return {}

    if not isinstance(raw, Mapping):
        raise ValueError(f"{section_name} must be a TOML table")  # noqa: TRY003, TRY004  # tracked: #288

    parsed: dict[FlagT, bool] = {}
    for key, value in raw.items():
        try:
            flag = flag_type(str(key))
        except (TypeError, ValueError):
            valid = _format_valid_flags(flag_type)
            raise ValueError(f"Unknown feature flag {key!r}. Supported flags: {valid}") from None  # noqa: TRY003  # tracked: #288

        if not isinstance(value, bool):
            raise ValueError(f"{section_name}.{key} must be true or false")  # noqa: TRY003, TRY004  # tracked: #288

        parsed[flag] = value

    return parsed


def _format_valid_flags(flag_type: type[FlagT]) -> str:
    values = [flag.value for flag in flag_type]
    if not values:
        return "none"
    return ", ".join(values)
