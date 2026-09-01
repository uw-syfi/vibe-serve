from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from vibesys.sandbox.task_image import (
    SubprocessDockerBuildRunner,
    TaskImageBuildError,
    build_task_image,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_IMAGE_ID = "sha256:" + "a" * 64


class FakeDockerBuildRunner:
    def __init__(
        self,
        *,
        result: subprocess.CompletedProcess[str] | BaseException | None = None,
        image_id: str | None = _IMAGE_ID,
    ) -> None:
        self.result = result or subprocess.CompletedProcess(("docker",), 0, "", "")
        self.image_id = image_id
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []
        self.iidfile: Path | None = None

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        normalized = tuple(argv)
        self.calls.append((normalized, cwd, timeout))
        self.iidfile = Path(normalized[normalized.index("--iidfile") + 1])
        if isinstance(self.result, BaseException):
            raise self.result
        if self.image_id is not None:
            self.iidfile.write_text(self.image_id, encoding="utf-8")
        return self.result


def _task(tmp_path: Path) -> Path:
    root = tmp_path / "task"
    root.mkdir()
    (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    return root


def test_build_uses_task_context_and_returns_immutable_image_id(tmp_path: Path) -> None:
    task_root = _task(tmp_path)
    runner = FakeDockerBuildRunner()

    assert (
        build_task_image(task_root / "Dockerfile", command_runner=runner, timeout=42) == _IMAGE_ID
    )

    argv, cwd, timeout = runner.calls[0]
    assert argv == (
        "docker",
        "build",
        "--iidfile",
        str(runner.iidfile),
        "--file",
        str(task_root / "Dockerfile"),
        str(task_root),
    )
    assert cwd == task_root
    assert timeout == 42
    assert runner.iidfile is not None
    assert not runner.iidfile.parent.exists()


def test_subprocess_runner_uses_no_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = MagicMock(return_value=subprocess.CompletedProcess(("docker",), 0, "", ""))
    monkeypatch.setattr("vibesys.sandbox.task_image.subprocess.run", run)

    SubprocessDockerBuildRunner().run(("docker", "build", "."), cwd=tmp_path, timeout=5)

    assert run.call_args.args == (("docker", "build", "."),)
    assert run.call_args.kwargs == {
        "cwd": tmp_path,
        "capture_output": True,
        "check": False,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 5,
    }


@pytest.mark.parametrize(
    ("failure", "match"),
    [
        (FileNotFoundError(), "Docker was not found"),
        (
            subprocess.TimeoutExpired(("docker", "build"), 7),
            "timed out after 7 seconds",
        ),
    ],
)
def test_build_translates_process_failures(
    tmp_path: Path, failure: BaseException, match: str
) -> None:
    runner = FakeDockerBuildRunner(result=failure, image_id=None)

    with pytest.raises(TaskImageBuildError, match=match):
        build_task_image(_task(tmp_path) / "Dockerfile", command_runner=runner, timeout=7)

    assert runner.iidfile is not None
    assert not runner.iidfile.parent.exists()


def test_build_reports_bounded_docker_failure(tmp_path: Path) -> None:
    runner = FakeDockerBuildRunner(
        result=subprocess.CompletedProcess(("docker",), 17, "ignored", "specific failure"),
        image_id=None,
    )

    with pytest.raises(TaskImageBuildError, match=r"exit 17.*specific failure"):
        build_task_image(_task(tmp_path) / "Dockerfile", command_runner=runner)


@pytest.mark.parametrize(
    ("image_id", "match"),
    [
        (None, "did not write its image ID"),
        ("", "invalid task image ID.*<empty>"),
        ("sha256:not-hex", "invalid task image ID"),
        ("sha256:" + "a" * 63, "invalid task image ID"),
        ("example:latest", "invalid task image ID"),
    ],
)
def test_build_rejects_missing_or_malformed_image_id(
    tmp_path: Path, image_id: str | None, match: str
) -> None:
    runner = FakeDockerBuildRunner(image_id=image_id)

    with pytest.raises(TaskImageBuildError, match=match):
        build_task_image(_task(tmp_path) / "Dockerfile", command_runner=runner)

    assert runner.iidfile is not None
    assert not runner.iidfile.parent.exists()


def test_build_validates_dockerfile_before_running_docker(tmp_path: Path) -> None:
    runner = FakeDockerBuildRunner()

    task_root = tmp_path / "task"
    task_root.mkdir()
    with pytest.raises(TaskImageBuildError, match="Dockerfile must be a regular file"):
        build_task_image(task_root / "Dockerfile", command_runner=runner)
    (task_root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    with pytest.raises(ValueError, match="timeout must be positive"):
        build_task_image(task_root / "Dockerfile", command_runner=runner, timeout=0)
    assert runner.calls == []


def test_build_rejects_dockerfile_symlink_without_widening_context(tmp_path: Path) -> None:
    task_root = tmp_path / "task"
    task_root.mkdir()
    outside = tmp_path / "outside.Dockerfile"
    outside.write_text("FROM scratch\n", encoding="utf-8")
    dockerfile = task_root / "Dockerfile"
    dockerfile.symlink_to(outside)
    runner = FakeDockerBuildRunner()

    with pytest.raises(TaskImageBuildError, match="must be a regular file"):
        build_task_image(dockerfile, command_runner=runner)

    assert runner.calls == []
