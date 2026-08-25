"""Implementation-neutral contracts for stateful agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import timedelta
    from pathlib import Path

    from pydantic import BaseModel

    from vs_sandbox import HostResource, ProjectPathPolicy


class SessionDisposition(StrEnum):
    """Whether a session remains safe to use after a turn."""

    REUSABLE = "reusable"
    RESET_REQUIRED = "reset_required"


class AgentEventKind(StrEnum):
    """Semantic categories streamed by every driver."""

    TEXT = "text"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    USAGE = "usage"


@dataclass(frozen=True, slots=True)
class AgentExecutionPolicy:
    """Existing sandbox semantics that a driver must enforce for a session.

    Drivers must reject policies they cannot fully enforce. The reusable
    ``vs_sandbox`` library remains the authority for project-path and host-
    resource semantics; this value only attaches them to agent setup.
    """

    project_paths: ProjectPathPolicy | None = None
    host_resources: tuple[HostResource, ...] = ()
    require_enforcement: bool = True
    containerized: bool = False


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    """Provider-independent description of a session-scoped stdio MCP server."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Features a driver can provide without weakening requested semantics."""

    mcp_servers: bool = False
    in_process_tools: bool = False
    nested_read_only_paths: bool = False
    hidden_paths: bool = False
    host_path_grants: bool = False
    container_execution: bool = False
    timeouts: bool = False
    session_reuse: bool = True


@dataclass(frozen=True, slots=True)
class AgentUsage:
    """Provider-independent token and cost accounting for one turn."""

    input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    output_tokens: int | None = None
    total_cost_usd: float | None = None
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """One normalized event emitted while an agent turn is running."""

    kind: AgentEventKind
    text: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)
    usage: AgentUsage | None = None


class AgentObserver(Protocol):
    """Consumer of normalized streaming events from an agent driver."""

    def on_event(self, event: AgentEvent) -> None:
        """Observe one event in driver emission order."""
        ...


@dataclass(frozen=True, slots=True)
class AgentSessionSpec:
    """Immutable configuration installed once for a conversation."""

    role: str
    provider: str
    workspace: Path
    policy: AgentExecutionPolicy
    model: str | None = None
    mcp_servers: tuple[MCPServerSpec, ...] = ()
    skills: tuple[Path, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    reasoning_effort: str | None = None


@dataclass(frozen=True, slots=True)
class AgentTurnRequest:
    """Context added to an existing conversation for one agent turn."""

    message: str
    instructions: str = ""
    output_schema: type[BaseModel] | None = None
    timeout: timedelta | None = None
    invocation_id: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    """Provider-independent result of one raw agent turn."""

    text: str
    usage: AgentUsage = field(default_factory=AgentUsage)
    provider_session_id: str | None = None
    disposition: SessionDisposition = SessionDisposition.REUSABLE


class AgentSession(Protocol):
    """A configured agent conversation owned by an :class:`AgentDriver`."""

    def run_turn(
        self,
        request: AgentTurnRequest,
        observer: AgentObserver | None = None,
    ) -> AgentTurnResult:
        """Add one turn to the conversation and return its raw result."""
        ...

    def close(self) -> None:
        """Release session resources. Implementations must be idempotent."""
        ...


class AgentDriver(Protocol):
    """Adapter that creates configured sessions using one execution system."""

    @property
    def capabilities(self) -> AgentCapabilities:
        """Return the features this driver can enforce."""
        ...

    def create_session(self, spec: AgentSessionSpec) -> AgentSession:
        """Create a session or reject any unsupported part of ``spec``."""
        ...

    def close(self) -> None:
        """Release driver resources. Implementations must be idempotent."""
        ...
