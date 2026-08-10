"""Static contracts for the release publishing workflow."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_publish_workflow_pins_every_setup_uv_binary_version() -> None:
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / ".github" / "workflows" / "publish.yml").read_text()
    )
    setup_uv_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    ]

    assert setup_uv_steps
    assert {step.get("with", {}).get("version") for step in setup_uv_steps} == {"0.9.24"}


def test_publish_workflow_runs_pinned_auditwheel_on_linux_release_wheels() -> None:
    workflow_text = (
        Path(__file__).parents[1] / ".github" / "workflows" / "publish.yml"
    ).read_text()

    assert "uv run auditwheel show" in workflow_text
    assert "uv run --with auditwheel" not in workflow_text
