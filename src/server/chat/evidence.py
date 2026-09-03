"""Durable, read-only trajectory snapshots for experiment chat."""

from __future__ import annotations

import shutil
import threading
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from vs_project import Project, StateSnapshot

_TRAJECTORY_SUFFIXES = frozenset({".json", ".jsonl", ".log", ".md", ".txt"})
_TRAJECTORY_SYNC_LOCK = threading.Lock()


class TrajectoryEvidence:
    """Refresh the bounded project-state and log evidence exposed to chat."""

    def __init__(  # noqa: PLR0913  # Snapshot inputs have distinct ownership roles.
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
        """Update chat evidence to a bounded snapshot of current run state."""
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
                trajectory_dir.mkdir(parents=True, exist_ok=True)
                # Converges to the same tree the old full rebuild produced:
                # unchanged files are left alone, changed ones are rewritten,
                # and anything this pass did not produce is pruned below.
                current = self._write_snapshot(
                    self._project.state.portable_run_export(self._run_id),
                    trajectory_dir / "state",
                )
                current |= self._copy_files(self._log_dir, trajectory_dir / "logs")
                self._prune_stale(trajectory_dir, current)
        except (OSError, ValueError) as exc:
            self._log(f"[warn] could not refresh experiment chat trajectory: {exc}")

    @staticmethod
    def _write_snapshot(snapshot: StateSnapshot, destination_root: Path) -> set[Path]:
        """Write changed state files, returning every destination now current."""
        current: set[Path] = set()
        for state_file in snapshot.files:
            if state_file.relative_path.suffix not in _TRAJECTORY_SUFFIXES:
                continue
            destination = destination_root.joinpath(*state_file.relative_path.parts)
            current.add(destination)
            # State files are held in memory already, so the skip compares the
            # bytes themselves rather than a stat proxy.
            if (
                not destination.is_symlink()
                and destination.is_file()
                and destination.stat().st_size == len(state_file.contents)
                and destination.read_bytes() == state_file.contents
            ):
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.tmp")
            temporary.write_bytes(state_file.contents)
            temporary.replace(destination)
        return current

    @staticmethod
    def _copy_files(source_root: Path, destination_root: Path) -> set[Path]:
        """Copy changed log files, returning every destination now current.

        Run logs are append-only: a writer adds bytes to the tail and never
        rewrites an earlier one, so a destination whose size and mtime both
        match its source holds the same content and is skipped without being
        read. ``copy2`` carries the source mtime across so the next sync has
        that comparison to make.

        The assumption degrades on a filesystem with coarse timestamps: an
        append that lands inside the destination's mtime granularity and leaves
        the size unchanged (a rewrite of exactly as many bytes) would be missed
        until the next write moves either. Nothing under ``log_dir`` rewrites
        in place today, and a missed byte costs chat one stale evidence file,
        never a wrong answer about state, so the skip is worth the full re-copy
        of every log on every question that it replaces.
        """
        current: set[Path] = set()
        if not source_root.is_dir():
            return current
        for source in sorted(source_root.rglob("*")):
            if (
                not source.is_file()
                or source.is_symlink()
                or source.suffix not in _TRAJECTORY_SUFFIXES
            ):
                continue
            destination = destination_root / source.relative_to(source_root)
            current.add(destination)
            source_stat = source.stat()
            if not destination.is_symlink() and destination.is_file():
                destination_stat = destination.stat()
                if (
                    destination_stat.st_size == source_stat.st_size
                    and destination_stat.st_mtime_ns == source_stat.st_mtime_ns
                ):
                    continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.tmp")
            shutil.copy2(source, temporary)
            temporary.replace(destination)
        return current

    @staticmethod
    def _prune_stale(trajectory_dir: Path, current: set[Path]) -> None:
        """Delete snapshot entries this sync did not produce."""
        # Deepest first, so a directory is only considered once its own entries
        # are gone; ``rmdir`` then removes exactly the ones left empty.
        for entry in sorted(trajectory_dir.rglob("*"), reverse=True):
            if entry.is_symlink() or entry.is_file():
                if entry not in current:
                    entry.unlink(missing_ok=True)
            elif entry.is_dir():
                with suppress(OSError):
                    entry.rmdir()
