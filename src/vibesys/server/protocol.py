"""Versioned transport-neutral contracts for supervision clients."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

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


class SnapshotQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.snapshot"] = "query.snapshot"


class ChatQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.chat"] = "query.chat"
    text: str


class HistoryQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.history"] = "query.history"


class PerformanceQuery(Request):  # noqa: D101  # tracked: #288
    type: Literal["query.performance"] = "query.performance"


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
    | SnapshotQuery
    | ChatQuery
    | HistoryQuery
    | PerformanceQuery
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
    action: Literal["pause", "resume"]
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


class Response(ProtocolModel):  # noqa: D101  # tracked: #288
    protocol_version: Literal[1] = PROTOCOL_VERSION
    request_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ok: bool = True
    error: str | None = None
    ack: CommandAck | None = None
    chat: ChatResult | None = None
    snapshot: RunSnapshot | None = None
    events: list[RunEvent] = Field(default_factory=list)
    performance: list[PerformanceRound] = Field(default_factory=list)


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


ServerMessage = Annotated[
    SubscribedMessage | EventMessage | EventBatchMessage | ProtocolErrorMessage,
    Field(discriminator="type"),
]
