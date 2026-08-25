"""Shared isolation for vs-project filesystem tests."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_vibesys_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep machine-local project state inside each test's temporary directory."""
    monkeypatch.setenv(
        "VIBESYS_STATE_HOME",
        str(tmp_path.parent / f".vibesys-state-{tmp_path.name}"),
    )
