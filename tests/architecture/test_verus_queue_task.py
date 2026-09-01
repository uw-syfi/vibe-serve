from __future__ import annotations

import ast
import tomllib
from pathlib import Path


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
        "command": ["python3", ".vibesys/tasks/verus-mpmc-open/container.py", "check"],
        "timeout_seconds": 300,
    }
    assert manifest["benchmark"]["command"] == [
        "python3",
        ".vibesys/tasks/verus-mpmc-open/container.py",
        "benchmark",
    ]
    assert manifest["benchmark"]["result"] == {
        "json_argument": "--output-json",
        "metric": "total_ops_per_sec",
    }

    runner = (task / "runner.py").read_text(encoding="utf-8")
    container_runner = (task / "container.py").read_text(encoding="utf-8")
    ast.parse(runner)
    ast.parse(container_runner)
    assert '"cargo",\n            "verus",\n            "verify"' in runner
    assert "FORBIDDEN_PROOF_BYPASSES" in runner
    assert "package.metadata.verus.verify = true" in runner
    assert "queue-candidate.so" not in runner

    dockerfile = (task / "container" / "Dockerfile").read_text(encoding="utf-8")
    assert "ubuntu:24.04@sha256:" in dockerfile
    assert "linux/amd64" in container_runner
    assert "VERUS_VERSION=0.2026.08.30.b432e82" in dockerfile
    assert "VERUS_SHA256=067f5f72a457fe66b77c0c10b180f2a" in dockerfile
    assert "RUST_TOOLCHAIN=1.97.1-x86_64-unknown-linux-gnu" in dockerfile


def test_open_verus_mpmc_contract_is_exact_fifo() -> None:
    task = _task_root()
    objective = (task / "OBJECTIVE.md").read_text(encoding="utf-8")
    harness = (task / "harness" / "src" / "main.rs").read_text(encoding="utf-8")

    assert "exact linearizable bounded-FIFO semantics" in objective
    assert "does not permit capacity" in objective
    assert "reservation before publication" in objective
    assert "pub fn enqueue(&self, value: T) -> Result<(), T>" in objective
    assert "pub fn dequeue(&self) -> Option<T>" in objective
    assert "queue_verus_mpmc::MpmcFifo" in harness
    assert "producer_order_contract" in harness
    assert "consumer_conservation_contract" in harness
