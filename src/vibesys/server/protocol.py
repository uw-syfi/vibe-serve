"""Versioned transport-neutral contracts for supervision clients."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from vibesys.server.diagnostics import Diagnostic, DiagnosticScope, exception_to_diagnostic
from vibesys.server.events import RunEvent

PROTOCOL_VERSION = 1


class ProtocolModel(BaseModel):  # noqa: D101  # tracked: #288
    model_config = ConfigDict(extra="forbid")


class Request(ProtocolModel):  # noqa: D101  # tracked: #288
    protocol_version: Literal[1] = PROTOCOL_VERSION
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PauseCommand(Request):  # noqa: D101  # tracked: #288
    type: Literal["command.pause"] = "command.pause"
    mode: Literal["after_current_agent_call"] = "after_current_agent_call"


class ResumeCommand(Request):  # noqa: D101  # tracked: #288
    type: Literal["command.resume"] = "command.resume"


class SteerCommand(Request):  # noqa: D101  # tracked: #288
    type: Literal["command.steer"] = "command.steer"
    text: str = Field(min_length=1)


class SnapshotQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.snapshot"] = "query.snapshot"


class ChatQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.chat"] = "query.chat"
    text: str


class HistoryQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.history"] = "query.history"


class PerformanceQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.performance"] = "query.performance"


class ExperimentQuery(Request):
    """Request the hypothesis-level experiment log for the attached run."""

    type: Literal["query.experiments"] = "query.experiments"


class EventsQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.events"] = "query.events"
    after_sequence: int = Field(default=0, ge=0)
    timeout_ms: int = Field(default=0, ge=0, le=30_000)


class SubscribeRequest(Request):  # noqa: D101  # tracked: #288
    type: Literal["subscribe"] = "subscribe"
    after_sequence: int = Field(default=0, ge=0)


ProtocolRequest = Annotated[
    PauseCommand
    | ResumeCommand
    | SteerCommand
    | SnapshotQuery
    | ChatQuery
    | HistoryQuery
    | PerformanceQuery
    | ExperimentQuery
    | EventsQuery
    | SubscribeRequest,
    Field(discriminator="type"),
]


class RunSnapshot(ProtocolModel):  # noqa: D101  # tracked: #288
    protocol_version: Literal[1] = PROTOCOL_VERSION
    run_id: str
    sequence: int
    status: str
    agent_kind: str | None = None
    round_label: str | None = None


class CommandAck(ProtocolModel):  # noqa: D101  # tracked: #288
    action: Literal["pause", "resume", "steer"]
    status: Literal["pending", "consumed"]


class ChatResult(ProtocolModel):  # noqa: D101  # tracked: #288
    question: str
    answer: str
    effect: Literal["none"] = "none"


class PerformanceRound(ProtocolModel):  # noqa: D101  # tracked: #288
    round: int
    perf_metric: FiniteFloat
    perf_unit: str
    passed: bool
    profile_skipped: bool = False


class HypothesisRound(ProtocolModel):
    """One round belonging to a hypothesis, for the experiment-log drill-down."""

    round: int
    passed: bool
    reviewed: bool
    hypothesis_outcome: str | None = None
    perf_metric: FiniteFloat | None = None
    perf_unit: str | None = None
    commit: str | None = None
    official_evaluation: bool = False
    candidate_disposition: str | None = None


class HypothesisEntry(ProtocolModel):
    """One unit of investigation: a hypothesis and every round it spans.

    ``resolved_outcome`` is the terminal value the agent loop itself recorded
    for the closing round (``proven``, ``rejected``, or a ``HypothesisOutcome``
    member). It is copied, never recomputed, so the client cannot drift from
    the framework's resolution semantics.
    """

    hypothesis_id: str
    # False when the underlying records carry no ``hypothesis_id``, e.g. a log
    # directory written before hypothesis tracking. The row is still returned
    # so history stays complete; clients render it as an explicit placeholder.
    identified: bool = True
    claim: str | None = None
    action: str | None = None
    first_round: int
    last_round: int
    rounds: list[HypothesisRound] = Field(default_factory=list)
    resolved_outcome: str | None = None
    # ``pass``/``fail`` from the closing round, or None when independent review
    # was deferred by sparse-review policy and the round is still provisional.
    judge_verdict: Literal["pass", "fail"] | None = None
    perf_metric: FiniteFloat | None = None
    perf_unit: str | None = None
    # Change against the last measured round preceding this hypothesis. None
    # when either side is unmeasured or the baseline is zero.
    perf_delta_pct: FiniteFloat | None = None
    # Integration, not truth: whether a framework-owned gate accepted the
    # candidate or it was retained on the Pareto frontier. Deliberately
    # independent of ``resolved_outcome``.
    kept: bool = False
    active: bool = False


class Response(ProtocolModel):  # noqa: D101  # tracked: #288
    protocol_version: Literal[1] = PROTOCOL_VERSION
    request_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ok: bool = True
    error: str | None = None
    diagnostic: Diagnostic | None = None
    ack: CommandAck | None = None
    chat: ChatResult | None = None
    snapshot: RunSnapshot | None = None
    events: list[RunEvent] = Field(default_factory=list)
    performance: list[PerformanceRound] = Field(default_factory=list)
    experiments: list[HypothesisEntry] = Field(default_factory=list)

    @classmethod
    def from_exception(
        cls,
        request_id: str,
        error: BaseException,
        *,
        operation: str = "Request",
        scope: DiagnosticScope = DiagnosticScope.REQUEST,
        code: str | None = None,
    ) -> Response:
        """Build a failed response with consistent legacy and typed errors."""
        diagnostic = exception_to_diagnostic(error, scope=scope, operation=operation, code=code)
        return cls(request_id=request_id, ok=False, error=diagnostic.summary, diagnostic=diagnostic)


class SubscribedMessage(ProtocolModel):  # noqa: D101  # tracked: #288
    type: Literal["subscribed"] = "subscribed"
    request_id: str
    run_id: str
    latest_sequence: int


class EventMessage(ProtocolModel):  # noqa: D101  # tracked: #288
    type: Literal["event"] = "event"
    event: RunEvent


class EventBatchMessage(ProtocolModel):  # noqa: D101  # tracked: #288
    type: Literal["event_batch"] = "event_batch"
    events: list[RunEvent]


class ProtocolErrorMessage(ProtocolModel):  # noqa: D101  # tracked: #288
    type: Literal["protocol_error"] = "protocol_error"
    request_id: str | None = None
    code: str
    message: str
    diagnostic: Diagnostic | None = None

    @classmethod
    def from_exception(
        cls,
        error: BaseException,
        *,
        request_id: str | None = None,
        operation: str = "Protocol operation",
        scope: DiagnosticScope = DiagnosticScope.PROTOCOL,
        code: str | None = None,
    ) -> ProtocolErrorMessage:
        """Build a protocol error with consistent legacy and typed errors."""
        diagnostic = exception_to_diagnostic(error, scope=scope, operation=operation, code=code)
        return cls(
            request_id=request_id,
            code=diagnostic.code,
            message=diagnostic.summary,
            diagnostic=diagnostic,
        )


ServerMessage = Annotated[
    SubscribedMessage | EventMessage | EventBatchMessage | ProtocolErrorMessage,
    Field(discriminator="type"),
]
