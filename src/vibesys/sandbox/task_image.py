"""Build the conventional Docker image owned by a repository-native task."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

_DEFAULT_BUILD_TIMEOUT_SECONDS = 1200.0
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_DIAGNOSTIC_LIMIT = 1000


class TaskImageBuildError(RuntimeError):
    """Raised when Docker cannot produce a valid immutable task image."""


class DockerBuildRunner(Protocol):
    """Injectable process boundary for a Docker image build."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run one Docker command without a shell."""
        ...


class SubprocessDockerBuildRunner:
    """Run Docker directly and capture its diagnostics."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run one Docker command without invoking a shell."""
        return subprocess.run(  # noqa: S603
            tuple(argv),
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )


def build_task_image(
    dockerfile_path: Path,
    *,
    command_runner: DockerBuildRunner | None = None,
    timeout: float = _DEFAULT_BUILD_TIMEOUT_SECONDS,
) -> str:
    """Build a task Dockerfile and return its immutable Docker image ID.

    The task directory is the complete Docker build context. Candidate source,
    Git metadata, and other project inputs therefore cannot be copied into the
    image. Docker owns layer caching; VibeSys invokes the build once per launch
    and consumes the exact image ID Docker reports through ``--iidfile``.
    """
    # Keep the lexical parent as the build context. Resolving the Dockerfile
    # itself could silently widen the context to a symlink target elsewhere.
    dockerfile = dockerfile_path.expanduser().absolute()
    root = dockerfile.parent
    if not dockerfile.is_file() or dockerfile.is_symlink():
        raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
            f"Task Dockerfile must be a regular file: {dockerfile}"
        )
    if timeout <= 0:
        raise ValueError("task image build timeout must be positive")  # noqa: TRY003

    runner = command_runner or SubprocessDockerBuildRunner()
    with tempfile.TemporaryDirectory(prefix="vibesys-task-image-") as temporary:
        iidfile = Path(temporary) / "image-id"
        argv = (
            "docker",
            "build",
            "--iidfile",
            str(iidfile),
            "--file",
            str(dockerfile),
            str(root),
        )
        try:
            result = runner.run(argv, cwd=root, timeout=timeout)
        except FileNotFoundError as exc:
            raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
                f"Docker was not found while building task image: {dockerfile}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
                f"Docker task image build timed out after {timeout:g} seconds: {dockerfile}"
            ) from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "docker build failed").strip()
            raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
                f"Could not build task image from {dockerfile} "
                f"(exit {result.returncode}): {detail[:_DIAGNOSTIC_LIMIT]}"
            )
        try:
            image_id = iidfile.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
                f"Docker built task image but did not write its image ID: {dockerfile}"
            ) from exc
        if _IMAGE_ID.fullmatch(image_id) is None:
            displayed = image_id[:_DIAGNOSTIC_LIMIT] or "<empty>"
            raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
                f"Docker wrote an invalid task image ID for {dockerfile}: {displayed!r}"
            )
        return image_id
