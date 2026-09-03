"""Durable, read-only trajectory snapshots for experiment chat."""

from __future__ import annotations

import shutil
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from vs_project import Project, StateSnapshot

_TRAJECTORY_SUFFIXES = frozenset({".json", ".jsonl", ".log", ".md", ".txt"})
_TRAJECTORY_SYNC_LOCK = threading.Lock()


class TrajectoryEvidence:
    """Refresh the bounded project-state and log evidence exposed to chat."""

    def __init__(  # Snapshot inputs have distinct ownership roles.
        self,
        *,
        state_dir: Path,
        shared_state_dir: Path,
        log_dir: Path,
        project: Project,
        run_id: str,
        log: Callable[[str], None],
        flush_logs: Callable[[], None],
    ) -> None:
        """Configure the project state and log sources copied for chat."""
        self._state_dir = state_dir
        self._shared_state_dir = shared_state_dir
        self._log_dir = log_dir
        self._project = project
        self._run_id = run_id
        self._log = log
        self._flush_logs = flush_logs

    def refresh(self, instructions: str) -> None:
        """Replace chat evidence with a bounded snapshot of current run state."""
        trajectory_dir = self._shared_state_dir / "trajectory"
        if self._state_dir.is_symlink() or self._shared_state_dir.is_symlink():
            self._log(f"[warn] experiment chat state is a symlink: {self._state_dir}")
            return
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            (self._state_dir / "instructions.md").write_text(instructions, encoding="utf-8")
            self._flush_logs()
            with _TRAJECTORY_SYNC_LOCK:
                if trajectory_dir.is_symlink():
                    trajectory_dir.unlink()
                elif trajectory_dir.exists():
                    shutil.rmtree(trajectory_dir)
                trajectory_dir.mkdir(parents=True, exist_ok=True)
                self._write_snapshot(
                    self._project.state.portable_run_export(self._run_id),
                    trajectory_dir / "state",
                )
                self._copy_files(self._log_dir, trajectory_dir / "logs")
        except (OSError, ValueError) as exc:
            self._log(f"[warn] could not refresh experiment chat trajectory: {exc}")

    @staticmethod
    def _write_snapshot(snapshot: StateSnapshot, destination_root: Path) -> None:
        for state_file in snapshot.files:
            if state_file.relative_path.suffix not in _TRAJECTORY_SUFFIXES:
                continue
            destination = destination_root.joinpath(*state_file.relative_path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.tmp")
            temporary.write_bytes(state_file.contents)
            temporary.replace(destination)

    @staticmethod
    def _copy_files(source_root: Path, destination_root: Path) -> None:
        if not source_root.is_dir():
            return
        for source in sorted(source_root.rglob("*")):
            if (
                not source.is_file()
                or source.is_symlink()
                or source.suffix not in _TRAJECTORY_SUFFIXES
            ):
                continue
            destination = destination_root / source.relative_to(source_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.tmp")
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
