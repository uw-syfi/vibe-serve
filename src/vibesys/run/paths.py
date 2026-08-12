"""Frozen value objects for per-run paths and agent-facing commands."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunPaths:
    """Canonical host paths owned by one project run.

    ``run_log_path`` is the *current* log file.  ``switch_log_file``
    replaces the whole record (the dataclass is frozen) rather than
    mutating the field in place.
    """

    project_root: Path
    log_dir: Path
    run_log_path: Path

    @property
    def workspace(self) -> Path:
        """Return the project root, which is also the only agent workspace."""
        return self.project_root


@dataclass(frozen=True)
class RunCommands:
    """Evaluator commands and helper paths as agents should see them.

    Snapshot of ``RunEnvironmentView.paths`` taken once the run-environment
    session is open; the view's paths are fixed for the session lifetime.
    """

    judge_accuracy_command: str | None
    judge_benchmark_command: str | None
    profiler_support_agent_path: str | None
    profiler_benchmark_command: str | None
