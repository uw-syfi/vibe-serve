"""Deepagents implementation of :class:`AgentRunner`.

Wraps ``deepagents.create_deep_agent`` and the existing
``vibesys.agent_runner.run_typed_agent`` plumbing — no behavior
change vs. what the simple loop did before this abstraction landed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable  # noqa: TC003  # tracked: #288
from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import Any, TextIO, TypeVar

from deepagents import create_deep_agent
from langchain.agents.structured_output import AutoStrategy
from langchain_core.callbacks import BaseCallbackHandler  # noqa: TC002  # tracked: #288
from langchain_core.tools import BaseTool  # noqa: TC002  # tracked: #288
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from vibesys._agent_cli.base import MCPServerSpec  # noqa: TC001  # tracked: #288
from vibesys.agent_runner import (
    log_agent_config,
    run_agent,
    run_typed_agent,
)
from vibesys.agents.callbacks import AgentLogger
from vibesys.agents.progress import AgentProgress  # noqa: TC001  # tracked: #288

T = TypeVar("T", bound=BaseModel)


def _agent_label(kind: str) -> str:
    """Convert ``"perf_eval"`` to ``"Perf Eval"``, etc."""
    return kind.replace("_", " ").title()


class DeepAgentsRunner:
    """:class:`AgentRunner` backed by ``deepagents.create_deep_agent``."""

    backend_name = "deepagents"

    def __init__(  # noqa: ANN204, D107  # tracked: #288
        self,
        *,
        model: Any,  # noqa: ANN401  # tracked: #288
        backends: dict[str, Any],
        skills: list[str],
        model_name: str | None,
        run_log_file: TextIO | None,
    ):
        self._model = model
        self._backends = backends
        self._skills = skills
        self._model_name = model_name
        self._run_log_file = run_log_file
        # Cache the compiled agent graph separately from conversation state.
        # Tool objects may close over per-invocation policy (the issue-loop
        # tools do), so their identities are part of the signature rather
        # than just their names.
        self._agents: dict[str, Any] = {}
        self._agent_signatures: dict[str, tuple[Any, ...]] = {}
        self._checkpointers: dict[str, MemorySaver] = {}
        self._sessions: dict[str, tuple[MemorySaver, str]] = {}

    def _checkpointer(self, kind: str) -> MemorySaver:
        """Return the checkpointer shared by one kind's cached graphs."""
        checkpointer = self._checkpointers.get(kind)
        if checkpointer is None:
            checkpointer = MemorySaver()
            self._checkpointers[kind] = checkpointer
        return checkpointer

    def _session(
        self, *, kind: str, reuse_session: bool | None, session_key: str | None
    ) -> tuple[MemorySaver, str]:
        """Return a fresh thread by default, or a durable thread for a key."""
        checkpointer = self._checkpointer(kind)
        if not reuse_session or kind == "chat":
            return checkpointer, uuid.uuid4().hex
        key = f"{kind}:{session_key}" if session_key else kind
        if key not in self._sessions:
            self._sessions[key] = (checkpointer, uuid.uuid4().hex)
        return self._sessions[key]

    def _get_agent(
        self,
        *,
        kind: str,
        system_prompt: str,
        response_cls: type[BaseModel] | None = None,
        tools: list[BaseTool] | None = None,
    ) -> Any:  # noqa: ANN401  # tracked: #288
        """Return the cached graph, rebuilding it when its inputs change."""
        tool_signature = tuple(id(tool) for tool in (tools or []))
        signature = (
            system_prompt,
            tuple(self._skills),
            tool_signature,
            response_cls,
            id(self._model),
            id(self._backends[kind]),
        )
        if kind in self._agents and self._agent_signatures.get(kind) == signature:
            return self._agents[kind]

        kwargs: dict[str, Any] = {
            "model": self._model,
            "backend": self._backends[kind],
            "system_prompt": system_prompt,
            "skills": self._skills,
            "checkpointer": self._checkpointer(kind),
            "tools": tools,
        }
        if response_cls is not None:
            kwargs["response_format"] = AutoStrategy(response_cls)
        agent = create_deep_agent(**kwargs)
        self._agents[kind] = agent
        self._agent_signatures[kind] = signature
        return agent

    def invoke(  # noqa: D102, PLR0913  # tracked: #288
        self,
        *,
        kind: str,
        workspace: Path,  # noqa: ARG002 — backend already encapsulates cwd
        system_prompt: str,
        env: dict[str, str] | None = None,  # noqa: ARG002 — env on the BaseSandbox
        user_prompt: str,
        response_cls: type[T],
        fallback_factory: Callable[[], T],
        round_label: str,
        invocation_id: str | None = None,
        progress: AgentProgress | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,  # noqa: ARG002 — cli-only injection point; deepagents uses tools=
        tools: list[BaseTool] | None = None,
        reuse_session: bool | None = None,
        session_key: str | None = None,
    ) -> T:
        label = _agent_label(kind)

        _, thread_id = self._session(
            kind=kind,
            reuse_session=reuse_session,
            session_key=session_key,
        )
        agent = self._get_agent(
            kind=kind,
            system_prompt=system_prompt,
            response_cls=response_cls,
            tools=tools,
        )
        log_agent_config(agent, label, self._run_log_file)

        callbacks: list[BaseCallbackHandler] = [
            AgentLogger(
                log_file=self._run_log_file,
                model_name=self._model_name,
                agent_label=label,
                progress=progress,
                agent_kind=kind,
                round_label=round_label,
                invocation_id=invocation_id,
            )
        ]

        return run_typed_agent(
            agent,
            user_prompt,
            response_cls=response_cls,
            label=kind.upper(),
            fallback_factory=fallback_factory,
            callbacks=callbacks,
            thread_id=thread_id,
            round_label=round_label,
            log_file=self._run_log_file,
        )

    def invoke_text(  # noqa: PLR0913  # tracked: #288
        self,
        *,
        kind: str,
        workspace: Path,  # noqa: ARG002 — backend already encapsulates cwd
        system_prompt: str,
        env: dict[str, str] | None = None,  # noqa: ARG002 — env on the BaseSandbox
        user_prompt: str,
        round_label: str,
        invocation_id: str | None = None,
        progress: AgentProgress | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,  # noqa: ARG002 — cli-only
        tools: list[BaseTool] | None = None,
        reuse_session: bool | None = None,
        session_key: str | None = None,
    ) -> str:
        """Run a conversational agent without imposing a response schema."""
        _, thread_id = self._session(
            kind=kind,
            reuse_session=reuse_session,
            session_key=session_key,
        )
        label = _agent_label(kind)
        agent = self._get_agent(
            kind=kind,
            system_prompt=system_prompt,
            tools=tools,
        )
        log_agent_config(agent, label, self._run_log_file)
        callbacks: list[BaseCallbackHandler] = [
            AgentLogger(
                log_file=self._run_log_file,
                model_name=self._model_name,
                agent_label=label,
                progress=progress,
                agent_kind=kind,
                round_label=round_label,
                invocation_id=invocation_id,
            )
        ]
        return run_agent(
            agent,
            user_prompt,
            callbacks=callbacks,
            thread_id=thread_id,
            round_label=round_label,
            log_file=self._run_log_file,
        )
