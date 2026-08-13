"""Presentation-neutral supervision application service."""

from __future__ import annotations

from vibesys.server.events import EventType, RunEvent
from vibesys.server.inspector import RunInspector
from vibesys.server.protocol import (
    ChatQuery,
    ChatResult,
    CommandAck,
    EventsQuery,
    HistoryQuery,
    PauseCommand,
    PerformanceQuery,
    PerformanceRound,
    ProtocolRequest,
    Response,
    ResumeCommand,
    RunSnapshot,
    SnapshotQuery,
    SteerCommand,
)
from vibesys.server.supervisor import RunSupervisor  # noqa: TC001  # tracked: #288


class SupervisionService:
    """Authoritative message API consumed by every presentation client."""

    def __init__(self, supervisor: RunSupervisor):  # noqa: ANN204, D107  # tracked: #288
        self.supervisor = supervisor
        self.inspector = RunInspector(supervisor)

    def execute(self, request: ProtocolRequest) -> Response:  # noqa: D102, PLR0911  # tracked: #288
        if isinstance(request, PauseCommand):
            self.supervisor.pause_after_call()
            return Response(
                request_id=request.request_id,
                ack=CommandAck(action="pause", status="pending"),
            )
        if isinstance(request, ResumeCommand):
            self.supervisor.resume()
            return Response(
                request_id=request.request_id,
                ack=CommandAck(action="resume", status="consumed"),
            )
        if isinstance(request, SteerCommand):
            self.supervisor.steer(request.text)
            return Response(
                request_id=request.request_id,
                ack=CommandAck(action="steer", status="pending"),
            )
        if isinstance(request, ChatQuery):
            sequence = self.supervisor.snapshot().sequence
            answer = self.supervisor.chat(request.text)
            return Response(
                request_id=request.request_id,
                chat=ChatResult(question=request.text, answer=answer),
                events=self.supervisor.read_events(sequence),
            )
        if isinstance(request, HistoryQuery):
            self.supervisor.record(EventType.STATUS_QUERY, "/history")
            return Response(request_id=request.request_id, events=self.history_events())
        if isinstance(request, PerformanceQuery):
            self.supervisor.record(EventType.STATUS_QUERY, "/perf")
            return Response(request_id=request.request_id, performance=self.performance_rounds())
        if isinstance(request, SnapshotQuery):
            return Response(request_id=request.request_id, snapshot=self.snapshot())
        if isinstance(request, EventsQuery):
            timeout = request.timeout_ms / 1000 if request.timeout_ms else None
            events = (
                self.wait_for_events(request.after_sequence, timeout)
                if timeout is not None
                else self.events(request.after_sequence)
            )
            return Response(request_id=request.request_id, events=events)
        raise TypeError(f"Unsupported protocol request: {type(request).__name__}")  # noqa: TRY003  # tracked: #288

    def snapshot(self) -> RunSnapshot:  # noqa: D102  # tracked: #288
        return self.supervisor.snapshot()

    def events(self, after_sequence: int = 0) -> list[RunEvent]:  # noqa: D102  # tracked: #288
        return self.supervisor.read_events(after_sequence)

    def history_events(self) -> list[RunEvent]:  # noqa: D102  # tracked: #288
        return self.supervisor.read_history_events()

    def performance_rounds(self) -> list[PerformanceRound]:  # noqa: D102  # tracked: #288
        project_run = self.supervisor.project_run
        if project_run is None:
            return []
        manifest = project_run.project.state.load_run(project_run.run_id)
        if manifest.configuration.outer_loop != "agent":
            return []
        rounds: list[PerformanceRound] = []
        for record in project_run.project.state.load_rounds(project_run.run_id):
            if record.perf_metric is None or record.perf_unit is None:
                continue
            rounds.append(
                PerformanceRound(
                    round=record.round_number,
                    perf_metric=record.perf_metric,
                    perf_unit=record.perf_unit,
                    passed=record.passed,
                    profile_skipped=record.profile_skipped,
                )
            )
        return rounds

    def wait_for_events(self, after_sequence: int, timeout: float | None = None) -> list[RunEvent]:  # noqa: D102  # tracked: #288
        return self.supervisor.wait_for_events(after_sequence, timeout)
