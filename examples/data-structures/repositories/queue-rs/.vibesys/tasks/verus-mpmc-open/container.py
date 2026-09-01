from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TASK_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_ROOT.parents[2]
DOCKERFILE = TASK_ROOT / "container" / "Dockerfile"
IMAGE = "vibesys-verus-mpmc:0.2026.08.30-b432e82"


def _run(command: list[str]) -> None:
    try:
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("docker was not found; install Docker to run this task") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"container command failed with exit code {exc.returncode}") from exc


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"check", "benchmark"}:
        raise RuntimeError("usage: container.py check | benchmark [arguments]")

    _run(
        [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "--file",
            str(DOCKERFILE),
            "--tag",
            IMAGE,
            str(TASK_ROOT / "container"),
        ]
    )
    arguments = sys.argv[1:]
    requested_output: Path | None = None
    container_output: Path | None = None
    if "--output-json" in arguments:
        output_index = arguments.index("--output-json") + 1
        if output_index >= len(arguments):
            raise RuntimeError("--output-json requires a path")
        requested_output = Path(arguments[output_index])
        if not requested_output.is_absolute():
            requested_output = PROJECT_ROOT / requested_output
        temporary_root = PROJECT_ROOT / "target" / "verus-mpmc-task"
        temporary_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=temporary_root,
            prefix="container-result-",
            suffix=".json",
        )
        os.close(descriptor)
        container_output = Path(temporary_name)
        arguments[output_index] = str(container_output)

    command = [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        "CARGO_HOME=/tmp/vibesys-cargo-home",
        "--env",
        "VIBESYS_VERUS_TASK_TARGET=/tmp/vibesys-target",
        "--volume",
        f"{PROJECT_ROOT}:{PROJECT_ROOT}",
        "--workdir",
        str(PROJECT_ROOT),
        IMAGE,
        "python3",
        ".vibesys/tasks/verus-mpmc-open/runner.py",
        *arguments,
    ]
    try:
        _run(command)
        if requested_output is not None and container_output is not None:
            requested_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(container_output, requested_output)
    finally:
        if container_output is not None:
            container_output.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL - {exc}")
        raise SystemExit(1) from None
