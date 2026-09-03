"""Incremental trajectory-evidence sync for experiment chat."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from server.chat.evidence import TrajectoryEvidence

if TYPE_CHECKING:
    from pathlib import Path


def _evidence(tmp_path: Path) -> tuple[TrajectoryEvidence, Path, Path]:
    shared_state_dir = tmp_path / "workspace" / "_vibesys_chat"
    shared_state_dir.mkdir(parents=True, exist_ok=True)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    project = MagicMock()
    project.state.portable_run_export.return_value = SimpleNamespace(files=[])
    evidence = TrajectoryEvidence(
        state_dir=shared_state_dir,
        shared_state_dir=shared_state_dir,
        log_dir=log_dir,
        project=project,
        run_id="run-1",
        log=lambda _message: None,
        flush_logs=lambda: None,
    )
    return evidence, log_dir, shared_state_dir


def test_refresh_skips_unchanged_files_and_prunes_stale_ones(tmp_path: Path) -> None:
    evidence, log_dir, shared_state_dir = _evidence(tmp_path)
    (log_dir / "run.log").write_text("round 1\n", encoding="utf-8")

    evidence.refresh("instructions")

    trajectory = shared_state_dir / "trajectory"
    copied = trajectory / "logs" / "run.log"
    first_stat = copied.stat()
    stale = trajectory / "logs" / "stale.log"
    stale.write_text("left over\n", encoding="utf-8")

    evidence.refresh("instructions")

    # The unchanged source is not rewritten in place, while a file the sync did
    # not produce is pruned as the old full rebuild would have.
    second_stat = copied.stat()
    assert (second_stat.st_ino, second_stat.st_ctime_ns) == (
        first_stat.st_ino,
        first_stat.st_ctime_ns,
    )
    assert copied.read_text(encoding="utf-8") == "round 1\n"
    assert not stale.exists()


def test_refresh_recopies_a_log_that_grew(tmp_path: Path) -> None:
    evidence, log_dir, shared_state_dir = _evidence(tmp_path)
    source = log_dir / "run.log"
    source.write_text("round 1\n", encoding="utf-8")

    evidence.refresh("instructions")
    with source.open("a", encoding="utf-8") as stream:
        stream.write("round 2\n")
    evidence.refresh("instructions")

    copied = shared_state_dir / "trajectory" / "logs" / "run.log"
    assert copied.read_text(encoding="utf-8") == "round 1\nround 2\n"
    # copy2 carries the source mtime across, which is what makes the next
    # sync's size-and-mtime comparison meaningful.
    assert copied.stat().st_mtime_ns == source.stat().st_mtime_ns


def test_refresh_prunes_a_directory_the_run_no_longer_produces(tmp_path: Path) -> None:
    evidence, log_dir, shared_state_dir = _evidence(tmp_path)
    (log_dir / "run.log").write_text("round 1\n", encoding="utf-8")

    evidence.refresh("instructions")
    stale_dir = shared_state_dir / "trajectory" / "logs" / "attempt-1"
    stale_dir.mkdir(parents=True)
    (stale_dir / "old.log").write_text("gone\n", encoding="utf-8")

    evidence.refresh("instructions")

    assert not stale_dir.exists()
    assert (shared_state_dir / "trajectory" / "logs" / "run.log").is_file()
