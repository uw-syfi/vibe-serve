"""Guard the TypeScript quality rules enabled in `biome.json`.

Biome enforces these rules in CI, so this test does not re-check TypeScript
sources. It pins the rules and thresholds themselves: dropping one, or
loosening a threshold, is a deliberate decision that should show up as a
failing test rather than as silently vanished coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_RULES = {
    ("complexity", "noExcessiveCognitiveComplexity"): {"maxAllowedComplexity": 15},
    ("complexity", "noExcessiveLinesPerFunction"): {"maxLines": 80, "skipBlankLines": True},
    ("complexity", "useMaxParams"): {"max": 6},
    ("style", "noExcessiveLinesPerFile"): {"maxLines": 1600},
}


def load_biome_config() -> dict[str, Any]:
    return json.loads((REPO_ROOT / "biome.json").read_text(encoding="utf-8"))


def test_quality_rules_are_errors_with_the_documented_thresholds() -> None:
    rules = load_biome_config()["linter"]["rules"]

    for (group, name), options in EXPECTED_RULES.items():
        entry = rules[group][name]
        assert entry["level"] == "error", f"{group}/{name} must fail CI, not warn"
        assert entry["options"] == options


def test_quality_rules_have_no_overrides() -> None:
    assert "overrides" not in load_biome_config()
