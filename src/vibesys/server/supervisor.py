"""Thread-safe human controls at agent invocation boundaries."""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable, Generator  # noqa: TC003  # tracked: #288
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import TYPE_CHECKING, Any

from vibesys.server.diagnostics import (
    Diagnostic,
    DiagnosticRetryability,
    DiagnosticScope,
    DiagnosticSeverity,
    exception_detail,
    exception_to_diagnostic,
)
from vibesys.server.events import (
    AgentOutputChannel,
    AgentOutputChunkData,
    ChatData,
    EventData,
    EventStatus,
    EventStore,
    EventType,
    InvocationFinishedData,
    InvocationStartedData,
    OutputData,
    OutputStream,
    PhaseData,
    RunEvent,
    json_value,
    make_event,
)
from vibesys.server.protocol import RunSnapshot

_MAX_EXCEPTION_CHAIN = 8

if TYPE_CHECKING:
    from vs_project import Project, StateSnapshot


@dataclass(frozen=True)
class ProjectRunState:
    """Typed access to one run's canonical project state."""

    project: Project
    run_id: str

    def history_snapshots(self) -> tuple[StateSnapshot, ...]:
        """Return immutable snapshots used by read-only run inspection."""
        return tuple(
            self.project.state.portable_namespace(self.run_id, namespace).snapshot()
            for namespace in ("agent", "plain", "evolve")
        )


class RunSupervisor:
    """Own pause state, invocation metadata, and the run audit store."""

    def __init__(self) -> None:  # noqa: D107  # tracked: #288
        self._condition = threading.Condition()
        self._pause_after_call = False
        self._paused = False
        self._pending_steer: list[str] = []
        self._active_invocation: str | None = None
        self._run_status = "starting"
        self._store: EventStore | None = None
        self._audit_store: EventStore | None = None
        self._pending_events: list[RunEvent] = []
        self.log_dir: Path | None = None
        self._project_run: ProjectRunState | None = None
        self._current_kind: str | None = None
        self._current_round: str | None = None
        self._chat_handler: Callable[[str], str] | None = None
        self._presentation_local = threading.local()
        # An invocation and its terminal run failure often carry the same
        # exception. Keep one diagnostic object so both events identify the
        # same operator-visible failure without reformatting it at each layer.
        self._error_diagnostics: dict[int, tuple[BaseException, Diagnostic]] = {}

    @property
    def current_round(self) -> str | None:  # noqa: D102  # tracked: #288
        with self._condition:
            return self._current_round

    @property
    def project_run(self) -> ProjectRunState | None:
        """Return canonical project state when a run context has attached."""
        with self._condition:
            return self._project_run

    def attach(
        self,
        log_dir: Path,
        *,
        project: Project | None = None,
        run_id: str | None = None,
    ) -> None:
        """Attach event logging, optionally with canonical project-run state.

        The headless server first attaches a bootstrap event directory before
        CLI parsing creates a project. The run context later supplies both the
        project and run ID, which readers use for persisted run metadata.
        """
        if (project is None) != (run_id is None):
            raise ValueError("project and run_id must be provided together")  # noqa: TRY003  # tracked: #288
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = log_dir
        events_path = log_dir / "run-events.jsonl"
        with self._condition:
            if project is not None and run_id is not None:
                self._project_run = ProjectRunState(project, run_id)
            store = self._store
            if store is not None and run_id is not None:
                store.run_id = run_id
            if store is not None and (store.path == events_path or self._audit_store is not None):
                return
            if store is None:
                store = EventStore(events_path, run_id=run_id or log_dir.parent.name)
                self._store = store
                pending, self._pending_events = self._pending_events, []
            else:
                self._audit_store = EventStore(events_path, run_id=run_id or log_dir.parent.name)
                pending = store.read()
        for event in pending:
            (self._audit_store or store).append(event)
        if self._audit_store is None:
            self.record(EventType.SERVER_STARTED, status=EventStatus.ACTIVE)
        with self._condition:
            self._run_status = "running"

    def publish_output(self, stream: OutputStream, content: str, source: str = "backend") -> None:  # noqa: D102  # tracked: #288
        if not content:
            return
        self.record(
            EventType.OUTPUT,
            data=OutputData(stream=stream, source=source, content=content),
        )

    def publish_agent_output(  # noqa: D102  # tracked: #288
        self,
        content: str,
        *,
        channel: AgentOutputChannel = "assistant",
        agent_kind: str | None = None,
        round_label: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        if not content:
            return
        self.publish_presentation(
            EventType.AGENT_OUTPUT_CHUNK,
            AgentOutputChunkData(channel=channel, content=content),
            agent_kind=agent_kind,
            round_label=round_label,
            invocation_id=invocation_id,
        )

    def publish_presentation(
        self,
        event_type: EventType,
        data: EventData,
        *,
        agent_kind: str | None = None,
        round_label: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        """Record a presentation event enriched with the active invocation scope."""
        scoped_kind = getattr(self._presentation_local, "agent_kind", None)
        scoped_round = getattr(self._presentation_local, "round_label", None)
        scoped_invocation = getattr(self._presentation_local, "invocation_id", None)
        self.record(
            event_type,
            agent_kind=agent_kind or scoped_kind or self._current_kind,
            round_label=round_label or scoped_round or self._current_round,
            invocation_id=invocation_id or scoped_invocation or self._active_invocation,
            data=data,
        )

    def record(  # noqa: D102  # tracked: #288
        self,
        event_type: EventType,
        text: str = "",
        *,
        data: EventData | None = None,
        **fields: Any,  # noqa: ANN401  # tracked: #288
    ) -> RunEvent | None:
        event = make_event(event_type, text, data=data, **fields)
        with self._condition:
            store = self._store
            if store is None:
                self._pending_events.append(event)
                return event
        recorded = store.append(event)
        audit_store = self._audit_store
        if audit_store is not None:
            audit_store.append(event)
        return recorded

    def read_events(self, after_sequence: int = 0) -> list[RunEvent]:  # noqa: D102  # tracked: #288
        store = self._store
        return store.read(after_sequence) if store else []

    def read_history_events(self) -> list[RunEvent]:
        """Return the durable session history, including earlier attachments."""
        store = self._audit_store or self._store
        return store.read() if store else []

    def wait_for_events(self, after_sequence: int, timeout: float | None = None) -> list[RunEvent]:  # noqa: D102  # tracked: #288
        store = self._store
        return store.wait(after_sequence, timeout) if store else []

    def snapshot(self) -> RunSnapshot:  # noqa: D102  # tracked: #288
        with self._condition:
            store = self._store
            return RunSnapshot(
                run_id=store.run_id if store else "",
                sequence=store.last_sequence if store else 0,
                status="paused" if self._paused else self._run_status,
                agent_kind=self._current_kind,
                round_label=self._current_round,
            )

    def chat_agent_available(self) -> bool:
        """True when an agent-backed chat handler is installed for this run.

        The handler exists only for the lifetime of the run context, so chat
        asked during setup or after teardown has no agent to reach.
        """
        with self._condition:
            return self._chat_handler is not None

    def chat(self, text: str) -> str:  # noqa: D102  # tracked: #288
        with self._condition:
            handler = self._chat_handler
        if handler is None:
            from vibesys.server.inspector import RunInspector  # noqa: PLC0415  # tracked: #288

            # No agent is reachable, so say that rather than answering as if
            # this were the normal path. The keyword diagnostic is still worth
            # showing, but it is supporting detail, not the answer.
            answer = (
                "The experiment chat agent is not available for this run"
                f" ({self._chat_unavailable_reason()}), so this is a read-only"
                " summary from the recorded events rather than an answer.\n\n"
                + RunInspector(self).answer(text)
            )
        else:
            answer = handler(text)
        self.record(
            EventType.CHAT,
            text,
            status=EventStatus.ANSWERED,
            agent_kind="chat",
            round_label="experiment-chat",
            data=ChatData(answer=answer),
        )
        return answer

    def _chat_unavailable_reason(self) -> str:
        with self._condition:
            status = self._run_status
        if status in {"completed", "failed"}:
            return "the run has finished"
        return "the run has not finished starting up"

    def set_chat_handler(self, handler: Callable[[str], str] | None) -> None:
        """Install the current experiment's agent-backed chat handler."""
        with self._condition:
            self._chat_handler = handler

    @contextmanager
    def presentation_scope(
        self, *, agent_kind: str, round_label: str, invocation_id: str
    ) -> Generator[None]:
        """Tag side-channel presentation events without changing active run state."""
        previous = (
            getattr(self._presentation_local, "agent_kind", None),
            getattr(self._presentation_local, "round_label", None),
            getattr(self._presentation_local, "invocation_id", None),
        )
        self._presentation_local.agent_kind = agent_kind
        self._presentation_local.round_label = round_label
        self._presentation_local.invocation_id = invocation_id
        try:
            yield
        finally:
            (
                self._presentation_local.agent_kind,
                self._presentation_local.round_label,
                self._presentation_local.invocation_id,
            ) = previous

    def pause_after_call(self) -> None:  # noqa: D102  # tracked: #288
        with self._condition:
            self._pause_after_call = True
        self.record(EventType.CONTROL, "/pause", status=EventStatus.PENDING)

    def resume(self) -> None:  # noqa: D102  # tracked: #288
        with self._condition:
            self._paused = False
            self._pause_after_call = False
            self._condition.notify_all()
        self.record(EventType.CONTROL, "/resume", status=EventStatus.CONSUMED)

    def steer(self, text: str) -> None:
        """Queue an operator instruction for the next agent invocation.

        The message is drained and appended to the next agent's user prompt in
        :meth:`before_agent`. It applies whether the run is live or paused (in
        which case it takes effect when the run resumes).
        """
        with self._condition:
            self._pending_steer.append(text)
        self.record(EventType.CONTROL, f"/steer: {text}", status=EventStatus.PENDING)

    def before_agent(  # noqa: D102  # tracked: #288
        self, kind: str, round_label: str, user_prompt: str, system_prompt: str = ""
    ) -> str:
        with self._condition:
            while self._paused:
                self._condition.wait()
            steer_messages = self._pending_steer
            self._pending_steer = []
            self._current_kind, self._current_round = kind, round_label
            invocation_id = uuid.uuid4().hex
            self._active_invocation = invocation_id

        effective_prompt = _with_steering(user_prompt, steer_messages)

        phase = PhaseData(phase=kind, attempt=_attempt_from_label(round_label))
        self.record(
            EventType.PHASE_STARTED,
            status=EventStatus.ACTIVE,
            agent_kind=kind,
            round_label=round_label,
            invocation_id=invocation_id,
            data=phase,
        )
        self.record(
            EventType.INVOCATION_STARTED,
            status=EventStatus.ACTIVE,
            agent_kind=kind,
            round_label=round_label,
            invocation_id=invocation_id,
            data=InvocationStartedData(system_prompt=system_prompt, user_prompt=effective_prompt),
        )
        if steer_messages:
            self.record(
                EventType.CONTROL,
                "/steer",
                status=EventStatus.CONSUMED,
                agent_kind=kind,
                round_label=round_label,
                invocation_id=invocation_id,
            )
        return effective_prompt

    def after_agent(  # noqa: D102  # tracked: #288
        self,
        kind: str,
        round_label: str,
        *,
        result: Any = None,  # noqa: ANN401  # tracked: #288
        error: BaseException | None = None,  # noqa: ANN401, RUF100  # tracked: #288
    ) -> None:
        with self._condition:
            invocation_id = self._active_invocation
            self._active_invocation = None
            should_pause = self._pause_after_call
            if should_pause:
                self._pause_after_call = False
                self._paused = True

        diagnostic = (
            self._diagnostic_for(error, DiagnosticScope.INVOCATION, operation="Agent invocation")
            if error
            else None
        )
        self.record(
            EventType.INVOCATION_FINISHED,
            status=EventStatus.FAILED if error else EventStatus.COMPLETED,
            agent_kind=kind,
            round_label=round_label,
            invocation_id=invocation_id,
            data=InvocationFinishedData(
                result=json_value(result), error=diagnostic.summary if diagnostic else None
            ),
            diagnostic=diagnostic,
        )
        self.record(
            EventType.PHASE_FINISHED,
            status=EventStatus.FAILED if error else EventStatus.COMPLETED,
            agent_kind=kind,
            round_label=round_label,
            invocation_id=invocation_id,
            data=PhaseData(phase=kind, attempt=_attempt_from_label(round_label)),
            diagnostic=diagnostic,
        )
        if should_pause:
            self.record(
                EventType.CONTROL,
                "/pause",
                status=EventStatus.CONSUMED,
                agent_kind=kind,
                round_label=round_label,
                invocation_id=invocation_id,
            )

    def status(self) -> str:  # noqa: D102  # tracked: #288
        with self._condition:
            state = "paused" if self._paused else self._run_status
            kind = self._current_kind or "starting"
            round_label = self._current_round or "no round yet"
        return f"{state} · {kind} · {round_label}"

    def finish(  # noqa: D102  # tracked: #288
        self,
        error: BaseException | None = None,
        *,
        record_event: bool = True,
        diagnostic: Diagnostic | None = None,
    ) -> None:
        event_diagnostic = diagnostic or (
            self._diagnostic_for(error, DiagnosticScope.RUN, operation="Run") if error else None
        )
        if error is not None and event_diagnostic is not None:
            # A terminal run failure is fatal, but it may originate at an
            # earlier boundary. Preserve that origin and stable identity so
            # consumers can coalesce the invocation, phase, and run events.
            event_diagnostic = event_diagnostic.model_copy(
                update={"severity": DiagnosticSeverity.FATAL}
            )
        with self._condition:
            self._run_status = "failed" if error else "completed"
            self._condition.notify_all()
            self._error_diagnostics.clear()
        if not record_event:
            return
        self.record(
            EventType.RUN_FAILED if error else EventType.RUN_FINISHED,
            event_diagnostic.summary if event_diagnostic else "",
            status=EventStatus.FAILED if error else EventStatus.COMPLETED,
            diagnostic=event_diagnostic,
        )

    def _diagnostic_for(
        self, error: BaseException, scope: DiagnosticScope, *, operation: str
    ) -> Diagnostic:
        """Return the canonical diagnostic for one exception instance."""
        key = id(error)
        with self._condition:
            for item in _exception_chain(error):
                cached = self._error_diagnostics.get(id(item))
                if cached is None or cached[0] is not item:
                    continue
                if item is error:
                    return cached[1]
                diagnostic = cached[1].model_copy(update={"detail": exception_detail(error)})
                self._error_diagnostics[key] = (error, diagnostic)
                return diagnostic
        diagnostic = exception_to_diagnostic(
            error,
            scope=scope,
            operation=operation,
            severity=DiagnosticSeverity.ERROR,
            retryability=DiagnosticRetryability.UNKNOWN,
        )
        with self._condition:
            self._error_diagnostics[key] = (error, diagnostic)
        return diagnostic


def _attempt_from_label(round_label: str) -> int | None:
    match = re.search(r"retry-(\d+)", round_label)
    return int(match.group(1)) if match else None


def _exception_chain(error: BaseException) -> list[BaseException]:
    """Follow causes and contexts without depending on diagnostic internals."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and len(chain) < _MAX_EXCEPTION_CHAIN:
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        chain.append(current)
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return chain


def _with_steering(user_prompt: str, messages: list[str]) -> str:
    """Append queued operator steering instructions to an agent's user prompt."""
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
