from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from vibesys.sandbox import modal_evaluator


def test_extract_modal_web_url_handles_rich_line_wrapping() -> None:
    output = """
    Created Web Function URL for Server.web_app =>
    │ https://workspace--vibesys-long-endpoint.moda
    │ l.run (label truncated)
    View Deployment: https://modal.com/apps/workspace/main/deployed/example
    """

    assert (
        modal_evaluator.extract_modal_web_url(output)
        == "https://workspace--vibesys-long-endpoint.modal.run"
    )


def test_extract_modal_web_url_handles_deploy_tree_wrapping() -> None:
    output = """
    ├── 🔨 Created web function fastapi_app =>
    │   https://vibeserve--vibesys-long-candidate-f51b76.moda
    │   l.run (label truncated)
    └── 🔨 Created function profile_remote.
    """

    assert (
        modal_evaluator.extract_modal_web_url(output)
        == "https://vibeserve--vibesys-long-candidate-f51b76.modal.run"
    )


def test_extract_modal_web_url_requires_endpoint() -> None:
    with pytest.raises(ValueError, match="did not print"):
        modal_evaluator.extract_modal_web_url("App deployed without a web function")


def test_extract_modal_app_identifier_handles_rich_line_wrapping() -> None:
    output = """
    View Deployment:
    │ https://modal.com/apps/workspace/main/deployed/vibesys-long-
    │ candidate
    """

    assert modal_evaluator.extract_modal_app_identifier(output) == "vibesys-long-candidate"


def test_run_evaluator_deploys_waits_and_injects_url(monkeypatch) -> None:
    deploy = SimpleNamespace(
        returncode=0,
        stdout="Web Function URL: https://workspace--candidate.modal.run\n",
        stderr="",
    )
    evaluator = SimpleNamespace(returncode=0)
    run = MagicMock(side_effect=[deploy, evaluator])
    wait = MagicMock()
    monkeypatch.setattr(modal_evaluator.subprocess, "run", run)
    monkeypatch.setattr(modal_evaluator, "wait_for_health", wait)

    result = modal_evaluator.run_evaluator(
        ["uv", "run", "python", "checker.py"],
        workspace="/workspace",
    )

    assert result == 0
    assert run.call_args_list == [
        call(
            ["uv", "run", "modal", "deploy", "/workspace/main.py"],
            cwd="/workspace",
            capture_output=True,
            text=True,
            check=False,
        ),
        call(
            [
                "uv",
                "run",
                "python",
                "checker.py",
                "--url",
                "https://workspace--candidate.modal.run",
            ],
            cwd="/workspace",
            check=False,
        ),
    ]
    wait.assert_called_once_with(
        "https://workspace--candidate.modal.run",
        timeout_seconds=90,
    )


def test_run_evaluator_prints_modal_logs_when_readiness_fails(
    monkeypatch,
    capsys,
) -> None:
    deploy = SimpleNamespace(
        returncode=0,
        stdout=(
            "Web Function URL: https://workspace--candidate.modal.run\n"
            "View Deployment: "
            "https://modal.com/apps/workspace/main/deployed/candidate-app\n"
        ),
        stderr="",
    )
    run = MagicMock(return_value=deploy)
    monkeypatch.setattr(modal_evaluator.subprocess, "run", run)
    monkeypatch.setattr(
        modal_evaluator,
        "wait_for_health",
        MagicMock(side_effect=TimeoutError("not ready")),
    )
    logs = MagicMock(return_value="RuntimeError: CUDA toolkit mismatch")
    monkeypatch.setattr(modal_evaluator, "recent_modal_logs", logs)

    result = modal_evaluator.run_evaluator(
        ["uv", "run", "python", "checker.py"],
        workspace="/workspace",
    )

    assert result == 1
    logs.assert_called_once_with("candidate-app", workspace="/workspace")
    assert "RuntimeError: CUDA toolkit mismatch" in capsys.readouterr().err
