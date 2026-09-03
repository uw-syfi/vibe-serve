"""Run lifecycle and human-control coordination."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any

from server.diagnostics import Diagnostic, DiagnosticScope, DiagnosticSeverity
from server.events import EventStatus, EventType

if TYPE_CHECKING:
    import threading

    from server.execution import ExecutionHandle, ExecutionTracker
    from server.journal import EventJournal
    from vs_project import Project, StateSnapshot


class RunStatus(StrEnum):
    """Lifecycle status of one run, as frontends observe it.

    This is the authoritative closed set for the ``status`` field of
    ``RunSnapshot``; the generated TypeScript protocol types derive their union
    from it.
    """

    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """Whether the run has settled into a status it never leaves."""
        match self:
            case RunStatus.COMPLETED | RunStatus.FAILED:
                return True
            case RunStatus.STARTING | RunStatus.RUNNING | RunStatus.PAUSED:
                return False


@dataclass(frozen=True)
class ProjectRunState:
    """Typed access to one attached canonical project run."""

    project: Project
    run_id: str

    def history_snapshots(self) -> tuple[StateSnapshot, ...]:
        """Return portable history snapshots relevant to frontend queries."""
        return tuple(
            self.project.state.portable_namespace(self.run_id, namespace).snapshot()
            for namespace in ("agent", "plain", "evolve")
        )


class RunController:
    """Coordinate run state, pause boundaries, steering, and terminal state."""

    def __init__(
        self,
        condition: threading.Condition,
        journal: EventJournal,
        executions: ExecutionTracker,
    ) -> None:
        """Initialize control state over the shared server condition."""
        self._condition = condition
        self._journal = journal
        self._executions = executions
        self._pause_after_call = False
        self._paused = False
        self._pending_steer: list[str] = []
        self._run_status: RunStatus = RunStatus.STARTING
        self._project_run: ProjectRunState | None = None

    @property
    def project_run(self) -> ProjectRunState | None:
        """Return the canonical project run attached to this controller."""
        with self._condition:
            return self._project_run

    @property
    def current_round(self) -> str | None:
        """Return the current controlled execution round."""
        return self._executions.current_round

    def attach(
        self,
        log_dir: Path,
        *,
        project: Project | None = None,
        run_id: str | None = None,
    ) -> None:
        """Attach durable run storage and optional canonical project state."""
        if project is not None and run_id is None:
            raise ValueError("run_id is required when project is provided")  # noqa: TRY003
        with self._condition:
            if project is not None and run_id is not None:
                self._project_run = ProjectRunState(project, run_id)
            self._journal.attach(log_dir, run_id=run_id)
            self._run_status = RunStatus.RUNNING

    def pause_after_call(self) -> None:
        """Request a pause after the current controlled invocation finishes."""
        with self._condition:
            self._pause_after_call = True
        self._journal.record(EventType.CONTROL, "/pause", status=EventStatus.PENDING)

    def resume(self) -> None:
        """Resume controlled invocations and clear a pending pause."""
        with self._condition:
            self._paused = False
            self._pause_after_call = False
            self._condition.notify_all()
        self._journal.record(EventType.CONTROL, "/resume", status=EventStatus.CONSUMED)

    def steer(self, text: str) -> None:
        """Queue operator guidance for the next controlled invocation."""
        with self._condition:
            self._pending_steer.append(text)
        self._journal.record(EventType.CONTROL, f"/steer: {text}", status=EventStatus.PENDING)

    def start_agent_execution(  # noqa: PLR0913
        self,
        kind: str,
        round_label: str,
        user_prompt: str,
        system_prompt: str = "",
        *,
        consume_steering: bool = True,
        participates_in_run_control: bool = True,
        emit_lifecycle: bool = True,
        driver: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> ExecutionHandle:
        """Enter one invocation boundary, applying pause and steering state."""
        with self._condition:
            while participates_in_run_control and self._paused:
                self._condition.wait()
            steering = (
                self._pending_steer if consume_steering and participates_in_run_control else []
            )
            if consume_steering and participates_in_run_control:
                self._pending_steer = []
            effective_prompt = _with_steering(user_prompt, steering)
            execution = self._executions.start_locked(
                kind,
                round_label,
                effective_prompt,
                system_prompt,
                participates_in_run_control=participates_in_run_control,
                emit_lifecycle=emit_lifecycle,
                driver=driver,
                provider=provider,
                model=model,
            )
            if steering:
                self._journal.record(
                    EventType.CONTROL,
                    "/steer",
                    status=EventStatus.CONSUMED,
                    agent_kind=kind,
                    round_label=round_label,
                    execution_id=execution.execution_id,
                )
            return execution

    def before_agent(
        self, kind: str, round_label: str, user_prompt: str, system_prompt: str = ""
    ) -> str:
        """Compatibility boundary returning only the effective prompt."""
        execution = self.start_agent_execution(kind, round_label, user_prompt, system_prompt)
        self._executions.remember_legacy(execution.execution_id)
        return execution.user_prompt

    def after_agent(
        self,
        kind: str,
        round_label: str,
        *,
        result: Any = None,  # noqa: ANN401
        error: BaseException | None = None,
        execution_id: str | None = None,
    ) -> None:
        """Finish an invocation and apply any pending pause transition."""
        del kind, round_label
        resolved_id = self._executions.resolve_legacy(execution_id)
        if resolved_id is None:
            with self._condition:
                if self._pause_after_call:
                    self._pause_after_call = False
                    self._paused = True
            return
        with self._condition:
            active, controlled = self._executions.finish_locked(
                resolved_id, result=result, error=error
            )
            if active is None:
                return
            should_pause = controlled and self._pause_after_call
            if should_pause:
                self._pause_after_call = False
                self._paused = True
                self._journal.record(
                    EventType.CONTROL,
                    "/pause",
                    status=EventStatus.CONSUMED,
                    agent_kind=active.agent_kind,
                    round_label=active.round_label,
                    execution_id=resolved_id,
                )
        self._executions.clear_legacy(resolved_id)

    def status(self) -> str:
        """Return a compact human-readable run status."""
        with self._condition:
            state = self.status_locked()
            kind, round_label = self._executions.current_locked()
        return f"{state} · {kind or 'starting'} · {round_label or 'no round yet'}"

    def status_locked(self) -> RunStatus:
        """Return run status while the caller holds the shared condition."""
        return RunStatus.PAUSED if self._paused else self._run_status

    def run_status(self) -> RunStatus:
        """Return the terminal-aware run status token."""
        with self._condition:
            return self._run_status

    def finish(
        self,
        error: BaseException | None = None,
        *,
        record_event: bool = True,
        diagnostic: Diagnostic | None = None,
    ) -> None:
        """Transition the run to its terminal state exactly once."""
        with self._condition:
            if self._run_status.is_terminal:
                return
            self._executions.interrupt_controlled_locked()
            self._run_status = RunStatus.FAILED if error else RunStatus.COMPLETED
            self._condition.notify_all()
        event_diagnostic = diagnostic
        if error is not None and event_diagnostic is not None:
            event_diagnostic = event_diagnostic.model_copy(
                update={"severity": DiagnosticSeverity.FATAL}
            )
        try:
            if not record_event:
                return
            if error is not None and event_diagnostic is None:
                self._journal.record_terminal_failure(
                    EventType.RUN_FAILED,
                    error,
                    scope=DiagnosticScope.RUN,
                    operation="Run",
                    severity=DiagnosticSeverity.FATAL,
                )
                return
            self._journal.record(
                EventType.RUN_FAILED if error else EventType.RUN_FINISHED,
                event_diagnostic.summary if event_diagnostic else "",
                status=EventStatus.FAILED if error else EventStatus.COMPLETED,
                diagnostic=event_diagnostic,
            )
        finally:
            self._journal.clear_diagnostics()


def _with_steering(user_prompt: str, messages: list[str]) -> str:
    if not messages:
        return user_prompt
    block = "\n".join(f"- {message}" for message in messages)
    return (
        f"{user_prompt.rstrip()}\n\n"
        "## Operator steering (live)\n\n"
        "The operator sent the following instruction(s) for this invocation. "
        "Treat them as high-priority guidance for the work you do now:\n\n"
        f"{block}\n"
    )
