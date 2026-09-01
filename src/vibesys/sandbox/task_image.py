"""Build the conventional Docker image owned by a repository-native task."""

from __future__ import annotations

import re
import subprocess
import uuid
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_DEFAULT_BUILD_TIMEOUT_SECONDS = 1200.0
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_DIAGNOSTIC_LIMIT = 1000
_TASK_IMAGE_REPOSITORY = "vibesys-task-build"


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
    and consumes the runnable manifest ID resolved from a unique local tag.
    Default provenance attestations are disabled because their generated
    metadata otherwise changes the manifest ID when all filesystem layers
    match. The unique tag remains as a local reachability anchor: containerd's
    image store cannot run the stable config digest emitted by ``--iidfile``
    when an otherwise untagged image has no retained manifest reference.
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
    image_tag = f"{_TASK_IMAGE_REPOSITORY}:{uuid.uuid4().hex}"
    build_argv = (
        "docker",
        "build",
        "--provenance=false",
        "--tag",
        image_tag,
        "--file",
        str(dockerfile),
        str(root),
    )
    build_result = _run_docker(
        runner,
        build_argv,
        timeout=timeout,
        action="building",
        dockerfile=dockerfile,
    )
    if build_result.returncode != 0:
        detail = (build_result.stderr or build_result.stdout or "docker build failed").strip()
        raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
            f"Could not build task image from {dockerfile} "
            f"(exit {build_result.returncode}): {detail[:_DIAGNOSTIC_LIMIT]}"
        )

    inspect_argv = (
        "docker",
        "image",
        "inspect",
        "--format",
        "{{.Id}}",
        image_tag,
    )
    inspect_result = _run_docker(
        runner,
        inspect_argv,
        timeout=timeout,
        action="inspecting",
        dockerfile=dockerfile,
    )
    if inspect_result.returncode != 0:
        detail = (inspect_result.stderr or inspect_result.stdout or "docker inspect failed").strip()
        raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
            f"Could not resolve runnable task image {image_tag} built from {dockerfile} "
            f"(exit {inspect_result.returncode}): {detail[:_DIAGNOSTIC_LIMIT]}"
        )
    image_id = inspect_result.stdout.strip()
    if _IMAGE_ID.fullmatch(image_id) is None:
        displayed = image_id[:_DIAGNOSTIC_LIMIT] or "<empty>"
        raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
            f"Docker returned an invalid runnable task image ID for {dockerfile}: {displayed!r}"
        )
    return image_id


def _run_docker(
    runner: DockerBuildRunner,
    argv: Sequence[str],
    *,
    timeout: float,
    action: str,
    dockerfile: Path,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner.run(argv, cwd=dockerfile.parent, timeout=timeout)
    except FileNotFoundError as exc:
        raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
            f"Docker was not found while {action} task image: {dockerfile}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TaskImageBuildError(  # noqa: TRY003  # external CLI diagnostic
            f"Docker timed out after {timeout:g} seconds while {action} task image: {dockerfile}"
        ) from exc
