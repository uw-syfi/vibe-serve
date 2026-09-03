"""Transport-neutral request API for frontend clients."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from server.api.experiments import build_experiment_log
from server.api.performance import (
    build_performance_context,
    metric_directions,
    summarize_objective,
)
from server.api.protocol import (
    ChatOptionsQuery,
    ChatQuery,
    ChatResult,
    ChatThreadCreateQuery,
    ChatThreadInfo,
    CommandAck,
    EventsQuery,
    ExperimentQuery,
    HistoryQuery,
    HypothesisEntry,
    PauseCommand,
    PerformanceContext,
    PerformanceQuery,
    PerformanceRound,
    ProtocolRequest,
    Response,
    ResumeCommand,
    RunSnapshot,
    SnapshotQuery,
    SteerCommand,
    TuiDefaultsQuery,
)
from server.chat.options import ChatOptions, build_chat_options
from server.events import EventType, RunEvent
from vibesys.loops.agent.hypotheses import reproject_run_evidence
from vibesys.loops.agent.state import AgentRunStateStore
from vs_project import ProjectStateError

if TYPE_CHECKING:
    from collections.abc import Callable

    from server.chat.manager import ChatManager
    from server.controller import RunController
    from server.execution import ActiveAgentExecution, ExecutionTracker
    from server.integration import RunIntegrationAdapter
    from server.journal import EventJournal
    from server.settings import InteractiveSetupDefaults
    from vibesys.loops.agent.model import AgentRunState


class RunApi:
    """Authoritative request API consumed by frontend clients."""

    def __init__(  # Explicit dependencies define the API boundary.
        self,
        condition: threading.Condition,
        controller: RunController,
        executions: ExecutionTracker,
        journal: EventJournal,
        chat: ChatManager,
        integration: RunIntegrationAdapter,
        *,
        tui_defaults: Callable[[], InteractiveSetupDefaults] | None = None,
    ) -> None:
        """Initialize the API with the components that own each request surface."""
        self._condition = condition
        self._controller = controller
        self._executions = executions
        self._journal = journal
        self._chat = chat
        self._integration = integration
        self._tui_defaults_provider = tui_defaults
        self._tui_defaults: InteractiveSetupDefaults | None = None
        self._tui_defaults_lock = threading.Lock()

    def execute(self, request: ProtocolRequest) -> Response:
        """Execute one typed request and return its protocol response."""
        if isinstance(request, (PauseCommand, ResumeCommand, SteerCommand)):
            return self._execute_command(request)
        if isinstance(request, ChatQuery):
            return self._execute_chat(request)
        if isinstance(request, ChatThreadCreateQuery):
            return self._execute_chat_thread_create(request)
        if isinstance(request, ChatOptionsQuery):
            return Response(request_id=request.request_id, chat_options=self.chat_options())
        if isinstance(request, TuiDefaultsQuery):
            return Response(request_id=request.request_id, tui_defaults=self.tui_defaults())
        if isinstance(request, HistoryQuery):
            self._journal.record(EventType.STATUS_QUERY, "/history")
            return Response(request_id=request.request_id, events=self.history_events())
        if isinstance(request, PerformanceQuery):
            self._journal.record(EventType.STATUS_QUERY, "/perf")
            return Response(
                request_id=request.request_id,
                performance=self.performance_rounds(),
                performance_context=self.performance_context(),
            )
        if isinstance(request, ExperimentQuery):
            self._journal.record(EventType.STATUS_QUERY, "/experiments")
            ready = self._controller.project_run is not None
            return Response(
                request_id=request.request_id,
                experiments=self.experiments() if ready else [],
                experiments_ready=ready,
            )
        if isinstance(request, SnapshotQuery):
            return Response(request_id=request.request_id, snapshot=self.snapshot())
        if isinstance(request, EventsQuery):
            timeout = request.timeout_ms / 1000 if request.timeout_ms else None
            events = (
                self.wait_for_events(request.after_sequence, timeout, request.before_sequence)
                if timeout is not None
                else self.events(request.after_sequence, request.before_sequence)
            )
            return Response(request_id=request.request_id, events=events)
        raise TypeError(  # noqa: TRY003  # Include the invalid protocol model in the error.
            f"Unsupported protocol request: {type(request).__name__}"
        )

    def _execute_command(self, request: PauseCommand | ResumeCommand | SteerCommand) -> Response:
        if isinstance(request, PauseCommand):
            self._controller.pause_after_call()
            ack = CommandAck(action="pause", status="pending")
        elif isinstance(request, ResumeCommand):
            self._controller.resume()
            ack = CommandAck(action="resume", status="consumed")
        else:
            self._controller.steer(request.text)
            ack = CommandAck(action="steer", status="pending")
        return Response(request_id=request.request_id, ack=ack)

    def _execute_chat(self, request: ChatQuery) -> Response:
        sequence = self._journal.latest_sequence
        answer = self._chat.chat(request.text, thread_id=request.thread_id)
        return Response(
            request_id=request.request_id,
            chat=ChatResult(question=request.text, answer=answer, thread_id=request.thread_id),
            events=self._journal.read(sequence),
        )

    def _execute_chat_thread_create(self, request: ChatThreadCreateQuery) -> Response:
        sequence = self._journal.latest_sequence
        spec = self._chat.create_thread(
            driver=request.driver,
            provider=request.provider,
            model=request.model,
            title=request.title,
        )
        return Response(
            request_id=request.request_id,
            chat_thread=ChatThreadInfo(
                thread_id=spec.thread_id,
                title=spec.title,
                driver=spec.driver,
                provider=spec.provider,
                model=spec.model,
            ),
            events=self._journal.read(sequence),
        )

    def chat_options(self) -> ChatOptions | None:
        """Return the agent choices available for experiment chat."""
        settings = self._chat.run_settings
        return None if settings is None else build_chat_options(settings)

    def tui_defaults(self) -> InteractiveSetupDefaults | None:
        """Load and cache defaults for the interactive setup form."""
        if self._tui_defaults_provider is None:
            return None
        with self._tui_defaults_lock:
            if self._tui_defaults is None:
                self._tui_defaults = self._tui_defaults_provider()
            return self._tui_defaults

    def snapshot(self) -> RunSnapshot:
        """Return a consistent snapshot of run and frontend-facing state."""
        with self._condition:
            kind, round_label = self._executions.current_locked()
            return RunSnapshot(
                run_id=self._journal.run_id_locked(),
                sequence=self._journal.latest_sequence_locked(),
                status=self._controller.status_locked(),
                agent_kind=kind,
                round_label=round_label,
                active_executions=self._executions.active_locked(),
                chat_threads=[
                    ChatThreadInfo(
                        thread_id=spec.thread_id,
                        title=spec.title,
                        driver=spec.driver,
                        provider=spec.provider,
                        model=spec.model,
                    )
                    for spec in self._chat.threads_locked()
                ],
            )

    def subscription_checkpoint(
        self, after_sequence: int, *, bootstrap_spine: bool = False
    ) -> tuple[int, list[RunEvent], list[ActiveAgentExecution]]:
        """Capture events and executions at one subscription sequence boundary."""
        with self._condition:
            through_sequence, events = self._journal.checkpoint_locked(
                after_sequence, bootstrap_spine=bootstrap_spine
            )
            return through_sequence, events, self._executions.active_locked()

    def events(self, after_sequence: int = 0, before_sequence: int | None = None) -> list[RunEvent]:
        """Read journal events within the requested sequence bounds."""
        return self._journal.read(after_sequence, before_sequence)

    def history_events(self) -> list[RunEvent]:
        """Read the canonical event history used by frontend clients."""
        return self._journal.read_history()

    def performance_rounds(self) -> list[PerformanceRound]:
        """Build the recorded round-level performance series."""
        state = self._agent_run_state()
        if state is None:
            return []
        return [
            PerformanceRound(
                round=record.round_number,
                perf_metric=record.perf_metric,
                perf_unit=record.perf_unit,
                passed=record.passed,
                profile_skipped=record.profile_skipped,
            )
            for record in state.rounds
            if record.perf_metric is not None and record.perf_unit is not None
        ]

    def performance_context(self) -> PerformanceContext | None:
        """Build objective and measurement context for performance rendering."""
        project_run = self._controller.project_run
        if project_run is None:
            return None
        manifest = project_run.project.state.load_run(project_run.run_id)
        if manifest.configuration.outer_loop != "agent":
            return None
        return build_performance_context(
            self._agent_run_state(),
            objectives=manifest.configuration.objectives,
            objective_description=self._objective_description(),
        )

    def experiments(self) -> list[HypothesisEntry]:
        """Build the experiment log for an agent outer loop."""
        state = self._agent_run_state()
        return [] if state is None else build_experiment_log(state)

    def wait_for_events(
        self,
        after_sequence: int,
        timeout: float | None = None,
        before_sequence: int | None = None,
    ) -> list[RunEvent]:
        """Wait for and read events newer than the supplied sequence."""
        return self._journal.wait_for_events(after_sequence, timeout, before_sequence)

    def wait_for_change(self, after_sequence: int, timeout: float | None = None) -> bool:
        """Wait until the journal advances beyond the supplied sequence."""
        return self._journal.wait_for_change(after_sequence, timeout)

    @property
    def latest_sequence(self) -> int:
        """Return the latest published journal sequence."""
        return self._journal.latest_sequence

    def _objective_description(self) -> str | None:
        project_run = self._controller.project_run
        if project_run is None:
            return None
        try:
            runtime = project_run.project.state.portable_namespace(project_run.run_id, "runtime")
            document = runtime.external_directory() / "effective-objective.md"
            if not document.is_file():
                return None
            text = document.read_text(encoding="utf-8")
        except (OSError, ProjectStateError):
            return None
        return summarize_objective(text)

    def _agent_run_state(self) -> AgentRunState | None:
        project_run = self._controller.project_run
        if project_run is None:
            return None
        manifest = project_run.project.state.load_run(project_run.run_id)
        if manifest.configuration.outer_loop != "agent":
            return None
        portable = project_run.project.state.portable_namespace(project_run.run_id, "agent")
        store = AgentRunStateStore(portable)
        state = store.load_optional()
        if state is None:
            from vibesys.run.state import RunStateNamespace  # noqa: PLC0415

            local = project_run.project.state.local_namespace(
                project_run.run_id, RunStateNamespace.AGENT
            )
            return store.migrate_legacy(
                rounds=project_run.project.state.load_rounds(project_run.run_id),
                local_namespace=local,
                legacy_directions=metric_directions(manifest.configuration.objectives),
            )
        return reproject_run_evidence(
            state, legacy_directions=metric_directions(manifest.configuration.objectives)
        )
