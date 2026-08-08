"""VibeSys's feature flag manifest.

The reusable flag machinery lives in the local ``vs-feature-flags`` package.
Declare VibeSys-specific flags here and add their definitions to ``FEATURES``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from vs_feature_flags import FeatureDefinition, FeatureRegistry


class FeatureFlag(StrEnum):  # noqa: D101  # tracked: #288
    EXAMPLE_FEATURE = "example_feature"
    OMNIGENT_AGENT_BACKEND = "omnigent_agent_backend"


FEATURES = FeatureRegistry(
    FeatureFlag,
    {
        FeatureFlag.EXAMPLE_FEATURE: FeatureDefinition(
            description="Exercise VibeSys feature flag plumbing.",
            default=False,
        ),
        FeatureFlag.OMNIGENT_AGENT_BACKEND: FeatureDefinition(
            description=(
                "Drive the cli agent backend through Omnigent's in-process "
                "Executor instead of agentshim. Opt-in and unproven: requires "
                "the 'omnigent' extra (Python 3.12+) and supports only the "
                "claude and codex providers on the host execution path."
            ),
            default=False,
        ),
    },
)


def is_feature_enabled(  # noqa: D103  # tracked: #288
    flag: FeatureFlag,
    config: object | None = None,
) -> bool:
    overrides = _feature_flag_overrides(config)
    return FEATURES.is_enabled(flag, overrides)


def _feature_flag_overrides(config: object | None) -> Mapping[FeatureFlag, bool]:
    if config is None:
        return {}

    raw_overrides = getattr(config, "feature_flags", None)
    if raw_overrides is None and isinstance(config, Mapping):
        raw_overrides = config.get("feature_flags", {})
    if raw_overrides is None:
        raw_overrides = {}
    if not isinstance(raw_overrides, Mapping):
        raise ValueError("config.feature_flags must be a mapping")  # noqa: TRY003, TRY004  # tracked: #288

    return raw_overrides
