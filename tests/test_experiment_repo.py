"""Tests for complete-experiment remote Git tracking."""

from __future__ import annotations

import subprocess
from pathlib import Path  # noqa: TC003  # tracked: #288

from vibesys.run import ExperimentRepository, RepositoryVisibility
from vs_github import GitHubCLI


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603  # tracked: #288
        ["git", *args],  # noqa: S607  # tracked: #288
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,  # noqa: RUF100, S607  # tracked: #288
    ).stdout.strip()


def test_sync_commits_durable_state_and_pushes_to_origin(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", remote], check=True)  # noqa: S603, S607  # tracked: #288

    experiment = tmp_path / "experiment"
    experiment.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=experiment, check=True)  # noqa: S607  # tracked: #288
    _git(experiment, "remote", "add", "origin", str(remote))
    (experiment / "workspace").mkdir()
    (experiment / "workspace" / "main.py").write_text("VALUE = 1\n")
    (experiment / "logs").mkdir()
    (experiment / "logs" / "state.json").write_text('{"round": 1}\n')
    (experiment / "logs" / "run-secret.log").write_text("provider output\n")

    messages: list[str] = []
    repository = ExperimentRepository(experiment, messages.append)
    repository.sync()

    assert _git(experiment, "log", "-1", "--format=%s") == "chore: sync experiment state"
    tracked = set(_git(experiment, "ls-files").splitlines())
    assert "workspace/main.py" in tracked
    assert "logs/state.json" in tracked
    assert "logs/run-secret.log" not in tracked
    assert _git(remote, "rev-parse", "refs/heads/main") == _git(experiment, "rev-parse", "HEAD")
    assert messages == ["[repo] pushed experiment state to origin"]


def test_sync_without_origin_is_a_noop(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=experiment, check=True)  # noqa: S607  # tracked: #288
    (experiment / "workspace").mkdir()
    (experiment / "workspace" / "main.py").write_text("VALUE = 1\n")

    messages: list[str] = []
    ExperimentRepository(experiment, messages.append).sync()

    assert _git(experiment, "status", "--short") == "?? workspace/"
    assert messages == []


def test_create_remote_uses_configurable_slug_and_visibility(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=experiment, check=True)  # noqa: S607  # tracked: #288
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001  # tracked: #288
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            2 if command[:4] == ["git", "remote", "get-url", "origin"] else 0,
            "",
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    repository = ExperimentRepository(
        experiment,
        lambda _message: None,
        github=GitHubCLI(_runner=fake_run),
    )
    repository.create_remote("vibesys-playground/example", RepositoryVisibility.INTERNAL)

    assert commands[-1][:5] == [
        "gh",
        "repo",
        "create",
        "vibesys-playground/example",
        "--internal",
    ]
    assert commands[-1][-2:] == ["--remote", "origin"]
