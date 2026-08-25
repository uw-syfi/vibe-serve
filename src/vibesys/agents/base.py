"""Backend-agnostic agent runner protocol.

Application runners live alongside this module:

- :mod:`vibesys.agents.deepagents_runner` wraps the existing
  ``deepagents`` + ``langchain`` stack used by every loop today.
- :class:`vibesys.agents.client.AgentClient` presents this interface over a
  stateful driver session. AgentShim and Omnigent are driver implementations.

Loops call a single ``invoke()`` method per (iteration × phase). Callers can
request a durable session by key without depending on a backend-specific
session object; the default remains each runner's historical behavior.
"""  # noqa: RUF002  # tracked: #288

from __future__ import annotations

from collections.abc import Callable  # noqa: TC003  # tracked: #288
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from pydantic import BaseModel

from vibesys._agent_cli.base import MCPServerSpec  # noqa: TC001  # tracked: #288
from vibesys.agents.contracts import AgentCapabilities  # noqa: TC001
from vibesys.agents.progress import AgentProgress  # noqa: TC001  # tracked: #288

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool  # annotation only; avoid eager agent-stack import

T = TypeVar("T", bound=BaseModel)


@dataclass(slots=True)
class ResponseFallback(Generic[T]):
    """A ``fallback_factory`` that records whether the runner had to use it.

    Every :meth:`AgentRunner.invoke` implementation calls ``fallback_factory``
    exactly when the agent's output could not be parsed into ``response_cls``,
    and returns the parsed response otherwise. Wrapping the factory turns that
    framework-side event into a typed signal the caller can read after the
    call, so a loop can tell a response it synthesized apart from one the agent
    actually authored.

    The signal deliberately lives here rather than on the response model: the
    response schema is sent to the agent as its structured-output contract, and
    a framework-only field would leak into it.

    Instances are single-call scoped — construct one per ``invoke()``::

        fallback = ResponseFallback(_missing_implementer_response)
        response = ctx.invoke(..., fallback_factory=fallback)
        if fallback.synthesized:
            ...
    """

    build: Callable[[], T]
    synthesized: bool = field(default=False, init=False)

    def __call__(self) -> T:
        """Build the fallback response and record that the runner needed it."""
        self.synthesized = True
        return self.build()


class AgentRunner(Protocol):
    """Backend-agnostic agent invoker. One instance per loop run."""

    backend_name: str
    """Diagnostic application backend identifier."""

    @property
    def capabilities(self) -> AgentCapabilities:
        """Return factual tool and execution capabilities."""
        ...

    def invoke(  # noqa: D417, PLR0913  # tracked: #288
        self,
        *,
        # static per-task config
        kind: str,
        workspace: Path,
        system_prompt: str,
        env: dict[str, str] | None = None,
        # dynamic per-call config
        user_prompt: str,
        response_cls: type[T],
        fallback_factory: Callable[[], T],
        round_label: str,
        invocation_id: str | None = None,
        progress: AgentProgress | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        tools: list[BaseTool] | None = None,
        reuse_session: bool | None = None,
        session_key: str | None = None,
    ) -> T:
        """Run an agent and return a structured response.

        Args:
            kind: One of ``"implementer"``, ``"judge"``, ``"perf_eval"``.
                The deepagents runner uses this to pick the right
                ``BaseSandbox`` from its backends dict; both backends use
                it to derive the human-facing label.
            workspace: Workspace root for this phase. The deepagents runner
                ignores it (its sandbox already encapsulates a working
                directory); the cli runner passes it through as ``cwd``
                to the underlying CLI process.
            system_prompt: Rendered Jinja2 system prompt (per phase).
            env: Optional environment overrides (e.g. ``CUDA_VISIBLE_DEVICES``).
            user_prompt: Rendered Jinja2 user prompt (per iteration).
            response_cls: Pydantic model class the agent should produce.
            fallback_factory: Constructs a default ``response_cls`` instance
                used when the agent fails to produce a parseable response.
                Implementations must call this rather than raise, and must
                call it only for that reason — callers may wrap it in
                :class:`ResponseFallback` to detect a synthesized response.
            round_label: Short label used in log headers (e.g. ``"judge #3"``).
            progress: Optional loop-owned progress state shown in the live
                terminal prefix (e.g. ``RoundProgress(3, 24)``).
            mcp_servers: Optional list of stdio MCP servers to install for
                the duration of this call. The cli runner forwards these to
                the underlying ``CodingAgent``'s
                :meth:`~vibesys._agent_cli.base.CodingAgent.install_mcp_servers`
                hook (then uninstalls in ``finally``); the deepagents runner
                ignores this kwarg.
            tools: Optional list of in-process LangChain tools to expose to
                the agent for the duration of this call. The deepagents
                runner forwards them to ``create_deep_agent(tools=...)``;
                the cli runner ignores this kwarg (the cli path uses
                ``mcp_servers`` for tool injection). Both kwargs are
                transport-level injection points and contain no domain
                knowledge — loops or wrappers populate them.
            reuse_session: Override the runner's historical session reuse
                policy. ``True`` continues the conversation identified by
                ``session_key``; ``False`` starts a clean session.
            session_key: Loop-owned stable identity for a reusable session.
                Defaults to ``kind`` when reuse is enabled.

        Returns:
            An instance of ``response_cls``, either parsed from the agent's
            output or produced by ``fallback_factory()``.
        """
        ...

    def invoke_text(  # noqa: PLR0913  # tracked: #288
        self,
        *,
        kind: str,
        workspace: Path,
        system_prompt: str,
        env: dict[str, str] | None = None,
        user_prompt: str,
        round_label: str,
        invocation_id: str | None = None,
        progress: AgentProgress | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        tools: list[BaseTool] | None = None,
        reuse_session: bool | None = None,
        session_key: str | None = None,
    ) -> str:
        """Run an agent and return its final message without a response schema."""
        ...

    def close(self) -> None:
        """Release runner resources. Implementations must be idempotent."""
        ...
