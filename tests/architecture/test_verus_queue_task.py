from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


def _task_root() -> Path:
    project_root = Path(__file__).parents[2]
    return (
        project_root
        / "examples"
        / "data-structures"
        / "repositories"
        / "queue-rs"
        / ".vibesys"
        / "tasks"
        / "verus-mpmc-open"
    )


def test_open_verus_mpmc_task_uses_pure_rust_runner() -> None:
    task = _task_root()
    manifest = tomllib.loads((task / "vibesys.input.toml").read_text(encoding="utf-8"))

    assert manifest["agent"] == {"domain": "generic"}
    assert "evaluator" not in manifest
    assert manifest["accuracy"] == {
        "command": ["python3", ".vibesys/tasks/verus-mpmc-open/runner.py", "check"],
        "timeout_seconds": 300,
    }
    assert manifest["benchmark"]["command"] == [
        "python3",
        ".vibesys/tasks/verus-mpmc-open/runner.py",
        "benchmark",
    ]
    assert manifest["benchmark"]["result"] == {
        "json_argument": "--output-json",
        "metric": "total_ops_per_sec",
    }

    runner = (task / "runner.py").read_text(encoding="utf-8")
    ast.parse(runner)
    assert '"cargo",\n            "verus",\n            "verify"' in runner
    assert "FORBIDDEN_PROOF_BYPASSES" in runner
    assert "FIXED_CANDIDATE_FILES" in runner
    assert "implementer modified fixed candidate file" in runner
    assert "package.metadata.verus.verify = true" in runner
    assert "queue-candidate.so" not in runner

    dockerfile = (task / "Dockerfile").read_text(encoding="utf-8")
    assert "VERUS_PLATFORM=linux/amd64" in dockerfile
    assert "ubuntu:24.04@sha256:" in dockerfile
    assert "git python3" in dockerfile
    assert "VERUS_VERSION=0.2026.08.30.b432e82" in dockerfile
    assert "VERUS_SHA256=067f5f72a457fe66b77c0c10b180f2a" in dockerfile
    assert "RUST_TOOLCHAIN=1.97.1-x86_64-unknown-linux-gnu" in dockerfile
    assert "ln -s /opt/cargo/bin/cargo /usr/local/bin/cargo" in dockerfile
    assert "ln -s /opt/verus/verus /usr/local/bin/verus" in dockerfile


def test_open_verus_mpmc_contract_is_exact_fifo() -> None:
    task = _task_root()
    objective = (task / "OBJECTIVE.md").read_text(encoding="utf-8")
    accuracy = (task / "accuracy" / "src" / "main.rs").read_text(encoding="utf-8")
    benchmark = (task / "benchmark" / "src" / "main.rs").read_text(encoding="utf-8")

    assert "exact linearizable bounded-FIFO semantics" in objective
    assert "does not permit capacity" in objective
    assert "reservation before publication" in objective
    assert "pub fn enqueue(&self, value: T) -> Result<(), T>" in objective
    assert "pub fn dequeue(&self) -> Option<T>" in objective
    assert "queue_verus_mpmc::MpmcFifo" in accuracy
    assert "producer_order_contract" in accuracy
    assert "consumer_conservation_contract" in accuracy
    assert "total_ops_per_sec=" not in accuracy
    assert "queue_verus_mpmc::MpmcFifo" in benchmark
    assert "total_ops_per_sec=" in benchmark
    assert "producer_order_contract" not in benchmark
    assert not any(path.is_file() for path in (task / "harness").rglob("*"))


@pytest.mark.parametrize(
    "relative_path",
    ["Cargo.toml", "src/lib.rs", "src/contract.rs", "src/api.rs"],
)
def test_open_verus_mpmc_rejects_fixed_file_changes(tmp_path: Path, relative_path: str) -> None:
    source_project = _task_root().parents[2]
    project = tmp_path / "queue-rs"
    task = project / ".vibesys" / "tasks" / "verus-mpmc-open"
    candidate = project / "verus-mpmc"
    task.mkdir(parents=True)
    shutil.copy2(_task_root() / "runner.py", task / "runner.py")
    shutil.copytree(
        source_project / "verus-mpmc",
        candidate,
        ignore=shutil.ignore_patterns("target"),
    )

    fixed_file = candidate / relative_path
    fixed_file.write_bytes(fixed_file.read_bytes() + b"\n")
    completed = subprocess.run(  # noqa: S603 - executes a copied repository script
        [sys.executable, str(task / "runner.py"), "check"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 1
    assert f"implementer modified fixed candidate file: {relative_path}" in completed.stdout


def test_open_verus_mpmc_readme_shows_vibesys_task_command() -> None:
    readme = (_task_root() / "README.md").read_text(encoding="utf-8")

    assert "vibesys --outer-loop agent" in readme
    assert "No separate `docker build`, `--docker`, or `--docker-image`" in readme
    assert "--project examples/data-structures/repositories/queue-rs" in readme
    assert "--runs-dir /absolute/path/to/vibesys-runs --local" in readme
    assert "--backend cpu --profiler none" in readme
    assert "detects the conventional task Dockerfile" in readme
    assert "automatically runs both agents and gates" in readme
