from __future__ import annotations

import json
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


def test_run_evaluator_deploys_waits_and_injects_url(monkeypatch) -> None:  # noqa: ANN001  # tracked: #288
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


def test_run_evaluator_reuses_healthy_deployment_for_exact_revision(
    monkeypatch,  # noqa: ANN001  # tracked: #288
    tmp_path,  # noqa: ANN001  # tracked: #288
) -> None:
    lease_path = tmp_path / "deployment.json"
    lease_path.write_text(
        json.dumps(
            {
                "candidate_revision": "abc123",
                "base_url": "https://workspace--candidate.modal.run",
            }
        )
    )
    evaluator = SimpleNamespace(returncode=0)
    run = MagicMock(return_value=evaluator)
    healthy = MagicMock(return_value=True)
    monkeypatch.setenv("VIBESYS_CANDIDATE_REVISION", "abc123")
    monkeypatch.setattr(modal_evaluator, "_DEPLOYMENT_LEASE_PATH", lease_path)
    monkeypatch.setattr(modal_evaluator, "_healthy_now", healthy)
    monkeypatch.setattr(modal_evaluator.subprocess, "run", run)

    result = modal_evaluator.run_evaluator(
        ["uv", "run", "python", "checker.py"],
        workspace="/workspace",
    )

    assert result == 0
    healthy.assert_called_once_with("https://workspace--candidate.modal.run")
    run.assert_called_once_with(
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
    )


def test_run_evaluator_releases_reused_deployment_after_final_gate(
    monkeypatch,  # noqa: ANN001  # tracked: #288
    tmp_path,  # noqa: ANN001  # tracked: #288
) -> None:
    lease_path = tmp_path / "deployment.json"
    lease_path.write_text(
        json.dumps(
            {
                "candidate_revision": "abc123",
                "base_url": "https://workspace--candidate.modal.run",
                "app_identifier": "candidate-app",
            }
        )
    )
    evaluator = SimpleNamespace(returncode=0)
    stop = SimpleNamespace(returncode=0, stdout="", stderr="")
    run = MagicMock(side_effect=[evaluator, stop])
    monkeypatch.setenv("VIBESYS_CANDIDATE_REVISION", "abc123")
    monkeypatch.setenv("VIBESYS_RELEASE_MODAL_DEPLOYMENT", "1")
    monkeypatch.setattr(modal_evaluator, "_DEPLOYMENT_LEASE_PATH", lease_path)
    monkeypatch.setattr(modal_evaluator, "_healthy_now", MagicMock(return_value=True))
    monkeypatch.setattr(modal_evaluator.subprocess, "run", run)

    result = modal_evaluator.run_evaluator(
        ["uv", "run", "python", "checker.py"],
        workspace="/workspace",
    )

    assert result == 0
    assert run.call_args_list[-1] == call(
        ["uv", "run", "modal", "app", "stop", "candidate-app", "--yes"],
        cwd="/workspace",
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert not lease_path.exists()


def test_run_evaluator_releases_new_deployment_after_final_gate(
    monkeypatch,  # noqa: ANN001  # tracked: #288
    tmp_path,  # noqa: ANN001  # tracked: #288
) -> None:
    lease_path = tmp_path / "deployment.json"
    deploy = SimpleNamespace(
        returncode=0,
        stdout=(
            "Web Function URL: https://workspace--candidate.modal.run\n"
            "View Deployment: "
            "https://modal.com/apps/workspace/main/deployed/candidate-app\n"
        ),
        stderr="",
    )
    evaluator = SimpleNamespace(returncode=0)
    stop = SimpleNamespace(returncode=0, stdout="", stderr="")
    run = MagicMock(side_effect=[deploy, evaluator, stop])
    monkeypatch.setenv("VIBESYS_CANDIDATE_REVISION", "abc123")
    monkeypatch.setenv("VIBESYS_RELEASE_MODAL_DEPLOYMENT", "1")
    monkeypatch.setattr(modal_evaluator, "_DEPLOYMENT_LEASE_PATH", lease_path)
    monkeypatch.setattr(modal_evaluator.subprocess, "run", run)
    monkeypatch.setattr(modal_evaluator, "wait_for_health", MagicMock())

    result = modal_evaluator.run_evaluator(
        ["uv", "run", "python", "checker.py"],
        workspace="/workspace",
    )

    assert result == 0
    assert run.call_args_list[-1] == call(
        ["uv", "run", "modal", "app", "stop", "candidate-app", "--yes"],
        cwd="/workspace",
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert not lease_path.exists()


def test_run_evaluator_stops_mismatched_leased_app_before_redeploy(
    monkeypatch,  # noqa: ANN001  # tracked: #288
    tmp_path,  # noqa: ANN001  # tracked: #288
) -> None:
    lease_path = tmp_path / "deployment.json"
    lease_path.write_text(
        json.dumps(
            {
                "candidate_revision": "old",
                "base_url": "https://workspace--old.modal.run",
                "app_identifier": "old-app",
            }
        )
    )
    stop = SimpleNamespace(returncode=0, stdout="", stderr="")
    deploy = SimpleNamespace(
        returncode=0,
        stdout="Web Function URL: https://workspace--new.modal.run\n",
        stderr="",
    )
    evaluator = SimpleNamespace(returncode=0)
    run = MagicMock(side_effect=[stop, deploy, evaluator])
    monkeypatch.setenv("VIBESYS_CANDIDATE_REVISION", "new")
    monkeypatch.setattr(modal_evaluator, "_DEPLOYMENT_LEASE_PATH", lease_path)
    monkeypatch.setattr(modal_evaluator.subprocess, "run", run)
    monkeypatch.setattr(modal_evaluator, "wait_for_health", MagicMock())

    result = modal_evaluator.run_evaluator(
        ["uv", "run", "python", "checker.py"],
        workspace="/workspace",
    )

    assert result == 0
    assert run.call_args_list[0] == call(
        ["uv", "run", "modal", "app", "stop", "old-app", "--yes"],
        cwd="/workspace",
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_run_evaluator_redeploys_and_replaces_mismatched_revision(
    monkeypatch,  # noqa: ANN001  # tracked: #288
    tmp_path,  # noqa: ANN001  # tracked: #288
) -> None:
    lease_path = tmp_path / "deployment.json"
    lease_path.write_text(
        json.dumps(
            {
                "candidate_revision": "old",
                "base_url": "https://workspace--old.modal.run",
            }
        )
    )
    deploy = SimpleNamespace(
        returncode=0,
        stdout="Web Function URL: https://workspace--new.modal.run\n",
        stderr="",
    )
    evaluator = SimpleNamespace(returncode=0)
    run = MagicMock(side_effect=[deploy, evaluator])
    monkeypatch.setenv("VIBESYS_CANDIDATE_REVISION", "new")
    monkeypatch.setattr(modal_evaluator, "_DEPLOYMENT_LEASE_PATH", lease_path)
    monkeypatch.setattr(modal_evaluator.subprocess, "run", run)
    monkeypatch.setattr(modal_evaluator, "wait_for_health", MagicMock())

    result = modal_evaluator.run_evaluator(
        ["uv", "run", "python", "checker.py"],
        workspace="/workspace",
    )

    assert result == 0
    assert json.loads(lease_path.read_text()) == {
        "candidate_revision": "new",
        "base_url": "https://workspace--new.modal.run",
    }


def test_run_evaluator_prints_modal_logs_when_readiness_fails(
    monkeypatch,  # noqa: ANN001  # tracked: #288
    capsys,  # noqa: ANN001  # tracked: #288
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
    assert (
        call(
            ["uv", "run", "modal", "app", "stop", "candidate-app", "--yes"],
            cwd="/workspace",
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        in run.call_args_list
    )
