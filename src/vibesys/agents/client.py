"""VibeSys agent service shared by all external-agent drivers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from vibesys.agent_runner import (
    log_and_print,
    log_json_and_print,
    log_markdown_and_print,
    log_prompt_markdown_and_print,
    parse_typed_response_text,
)
from vibesys.agents.cli_common import agent_label, materialize_skills
from vibesys.agents.contracts import (
    AgentCapabilities,
    AgentDriver,
    AgentEvent,
    AgentEventKind,
    AgentExecutionPolicy,
    AgentObserver,
    AgentSession,
    AgentSessionSpec,
    AgentTurnRequest,
    AgentTurnResult,
    AgentUsage,
    MCPServerSpec,
    SessionDisposition,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path
    from typing import TextIO

    from langchain_core.tools import BaseTool

    from vibesys._agent_cli.base import MCPServerSpec as LegacyMCPServerSpec
    from vibesys.agents.callbacks import AgentLogger
    from vibesys.agents.progress import AgentProgress
    from vibesys.constants import ComputeBackend
    from vs_sandbox import HostResource, ProjectPathPolicy

T = TypeVar("T", bound=BaseModel)


@dataclass(slots=True)
class _CachedSession:
    spec: AgentSessionSpec
    session: AgentSession


class AgentDiagnosticLog:
    """Mutable diagnostic destination shared by a client and its driver."""

    def __init__(self, stream: TextIO | None) -> None:
        """Create a log target backed by ``stream``."""
        self.stream = stream

    def __call__(self, message: str) -> None:
        """Write one driver diagnostic to the current run log."""
        log_and_print(message, self.stream)


class _LoggerObserver:
    """Translate neutral driver events into VibeSys's application logger."""

    def __init__(self, logger: AgentLogger) -> None:
        self._logger = logger

    def on_event(self, event: AgentEvent) -> None:
        """Render one normalized driver event."""
        if event.kind is AgentEventKind.TEXT:
            self._logger.log_text(event.text or "")
        elif event.kind is AgentEventKind.THINKING:
            self._logger.on_thinking(event.text or "")
        elif event.kind is AgentEventKind.TOOL_CALL:
            tool = str(event.payload.get("tool", "unknown"))
            args = event.payload.get("args")
            self._logger.on_tool_call(tool, args if isinstance(args, (dict, str)) else None)
        elif event.kind is AgentEventKind.TOOL_RESULT:
            exit_code = event.payload.get("exit_code")
            duration = event.payload.get("duration")
            self._logger.on_tool_result(
                str(event.payload.get("tool", "unknown")),
                stdout=str(event.payload.get("stdout", event.text or "")),
                stderr=str(event.payload.get("stderr", "")),
                exit_code=exit_code if isinstance(exit_code, int) else None,
                duration=float(duration) if isinstance(duration, (int, float)) else None,
            )
        elif event.kind is AgentEventKind.USAGE and event.usage is not None:
            self._logger.update_usage(_usage_dict(event.usage))


def _usage_dict(usage: AgentUsage) -> dict[str, int | float | None]:
    return {
        "input_tokens": usage.input_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "output_tokens": usage.output_tokens,
        "total_cost_usd": usage.total_cost_usd,
        "duration_ms": usage.duration_ms,
    }


def _normalize_mcp_servers(
    servers: Iterable[LegacyMCPServerSpec] | None,
) -> tuple[MCPServerSpec, ...]:
    if servers is None:
        return ()
    return tuple(
        MCPServerSpec(
            name=server.name,
            command=server.command,
            args=tuple(server.args),
            env=tuple(sorted(server.env.items())),
        )
        for server in servers
    )


class AgentClient:
    """Run agent turns and own the resulting configured sessions.

    A non-``None`` session key resumes a conversation only while its immutable
    session specification remains equal. Changing setup configuration closes
    and replaces the old session. An unkeyed turn uses an ephemeral session.
    """

    backend_name = "cli"

    def __init__(  # noqa: PLR0913
        self,
        driver: AgentDriver,
        *,
        provider: str | None = None,
        skills: Iterable[Path] = (),
        compute_backend: ComputeBackend | None = None,
        model_name: str | None = None,
        timeout: int | None = None,
        run_log_file: TextIO | None = None,
        log_dir: Path | None = None,
        default_reasoning_effort: str | None = None,
        role_models: Mapping[str, str] | None = None,
        role_reasoning_efforts: Mapping[str, str] | None = None,
        project_path_policy: ProjectPathPolicy | None = None,
        host_resources: Iterable[HostResource] = (),
        require_host_sandbox: bool = False,
        containerized: bool = False,
        driver_log: AgentDiagnosticLog | None = None,
    ) -> None:
        """Create a client that owns ``driver`` and every session it creates."""
        self._driver = driver
        self._provider = provider
        self._skills = tuple(skills)
        self._compute_backend = compute_backend
        self._model_name = model_name
        self._timeout = timeout
        self._run_log_file = run_log_file
        self._driver_log = driver_log
        self._log_dir = log_dir
        self._default_reasoning_effort = default_reasoning_effort
        self._role_models = dict(role_models or {})
        self._role_reasoning_efforts = dict(role_reasoning_efforts or {})
        self._policy = AgentExecutionPolicy(
            project_paths=project_path_policy,
            host_resources=tuple(host_resources),
            require_enforcement=require_host_sandbox,
            containerized=containerized,
        )
        self._sessions: dict[str, _CachedSession] = {}
        self._closed = False

    @property
    def capabilities(self) -> AgentCapabilities:
        """Return the selected driver's factual capabilities."""
        return self._driver.capabilities

    def set_log_file(self, stream: TextIO | None) -> None:
        """Direct subsequent application logs to ``stream``."""
        self._run_log_file = stream
        if self._driver_log is not None:
            self._driver_log.stream = stream

    def invoke(  # noqa: PLR0913
        self,
        *,
        kind: str,
        workspace: Path,
        system_prompt: str,
        user_prompt: str,
        response_cls: type[T],
        fallback_factory: Callable[[], T],
        round_label: str,
        env: dict[str, str] | None = None,
        invocation_id: str | None = None,
        progress: AgentProgress | None = None,
        mcp_servers: list[LegacyMCPServerSpec] | None = None,
        tools: list[BaseTool] | None = None,
        reuse_session: bool | None = None,
        session_key: str | None = None,
    ) -> T:
        """Run one turn and parse its structured response."""
        del tools  # In-process tools remain a deepagents-only compatibility path.
        result = self._invoke_turn(
            kind=kind,
            workspace=workspace,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=response_cls,
            round_label=round_label,
            env=env,
            invocation_id=invocation_id,
            progress=progress,
            mcp_servers=mcp_servers,
            reuse_session=reuse_session,
            session_key=session_key,
        )
        label = agent_label(kind)
        parsed = parse_typed_response_text(result.text, response_cls)
        if parsed is None:
            log_and_print(f"\n=== {label} ROUND OUTPUT (missing response) ===", self._run_log_file)
            log_and_print(
                f"No structured response received from {label.lower()}.",
                self._run_log_file,
            )
            if result.text:
                log_and_print(
                    f"\n=== {label} ROUND OUTPUT (raw output) ===",
                    self._run_log_file,
                )
                log_markdown_and_print(result.text, self._run_log_file)
            return fallback_factory()
        log_and_print(f"\n=== {label} ROUND OUTPUT ===", self._run_log_file)
        log_json_and_print(parsed.model_dump_json(indent=2), self._run_log_file)
        return parsed

    def invoke_text(  # noqa: PLR0913
        self,
        *,
        kind: str,
        workspace: Path,
        system_prompt: str,
        user_prompt: str,
        round_label: str,
        env: dict[str, str] | None = None,
        invocation_id: str | None = None,
        progress: AgentProgress | None = None,
        mcp_servers: list[LegacyMCPServerSpec] | None = None,
        tools: list[BaseTool] | None = None,
        reuse_session: bool | None = None,
        session_key: str | None = None,
    ) -> str:
        """Run one conversational turn without a structured-output requirement."""
        del tools
        result = self._invoke_turn(
            kind=kind,
            workspace=workspace,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=None,
            round_label=round_label,
            env=env,
            invocation_id=invocation_id,
            progress=progress,
            mcp_servers=mcp_servers,
            reuse_session=reuse_session,
            session_key=session_key,
        )
        label = agent_label(kind)
        if result.text:
            log_and_print(f"\n=== {label} ROUND OUTPUT ===", self._run_log_file)
            log_markdown_and_print(result.text, self._run_log_file)
        else:
            log_and_print(f"\n=== {label} ROUND OUTPUT (missing response) ===", self._run_log_file)
            log_and_print(f"No response received from {label.lower()}.", self._run_log_file)
        return result.text

    def _invoke_turn(  # noqa: PLR0913
        self,
        *,
        kind: str,
        workspace: Path,
        system_prompt: str,
        user_prompt: str,
        output_schema: type[BaseModel] | None,
        round_label: str,
        env: dict[str, str] | None,
        invocation_id: str | None,
        progress: AgentProgress | None,
        mcp_servers: list[LegacyMCPServerSpec] | None,
        reuse_session: bool | None,
        session_key: str | None,
    ) -> AgentTurnResult:
        label = agent_label(kind)
        model = self._role_models.get(kind, self._model_name)
        reasoning_effort = self._role_reasoning_efforts.get(kind, self._default_reasoning_effort)
        spec = AgentSessionSpec(
            role=kind,
            provider=self._provider or "codex",
            workspace=workspace,
            policy=self._policy,
            model=model,
            mcp_servers=_normalize_mcp_servers(mcp_servers),
            skills=self._skills,
            environment=tuple(sorted((env or {}).items())),
            reasoning_effort=reasoning_effort,
        )
        turn = AgentTurnRequest(
            message=user_prompt,
            instructions=system_prompt,
            output_schema=output_schema,
            timeout=timedelta(seconds=self._timeout) if self._timeout is not None else None,
            invocation_id=invocation_id,
            label=round_label,
        )
        from vibesys.agents.callbacks import AgentLogger  # noqa: PLC0415

        logger = AgentLogger(
            log_file=self._run_log_file,
            model_name=model,
            agent_label=label,
            progress=progress,
            agent_kind=kind,
            round_label=round_label,
            invocation_id=invocation_id,
        )
        log_and_print(f"\n=== {label} ROUND START: {round_label} ===", self._run_log_file)
        log_and_print(
            f"driver: {type(self._driver).__name__}, provider: {spec.provider}, "
            f"model: {model}, reasoning_effort: {reasoning_effort or 'provider_default'}, "
            f"cwd: {workspace}",
            self._run_log_file,
        )
        log_and_print("--- input ---", self._run_log_file)
        log_prompt_markdown_and_print(f"{system_prompt}\n\n{user_prompt}", self._run_log_file)
        reuse = kind != "chat" and (reuse_session if reuse_session is not None else True)
        cache_key = session_key or kind
        result: AgentTurnResult | None = None
        try:
            result = self.run(
                session_spec=spec,
                turn=turn,
                session_key=cache_key if reuse else None,
                observer=_LoggerObserver(logger),
            )
        except Exception as exc:
            log_and_print(f"\n=== {label} ROUND ERROR: {round_label} ===", self._run_log_file)
            log_and_print(f"{type(exc).__name__}: {exc}", self._run_log_file)
            raise
        finally:
            # The result may not exist after a setup/turn failure. The empty
            # record preserves one audit row per attempted invocation.
            self._write_usage_record(
                kind=kind,
                round_label=round_label,
                model=model,
                reasoning_effort=reasoning_effort,
                usage=result.usage if result is not None else AgentUsage(),
            )
        assert result is not None  # noqa: S101  # assigned or the exception propagated
        return result

    def _write_usage_record(
        self,
        *,
        kind: str,
        round_label: str,
        model: str | None,
        reasoning_effort: str | None,
        usage: AgentUsage,
    ) -> None:
        if self._log_dir is None:
            return
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "kind": kind,
            "round_label": round_label,
            "provider": self._provider,
            "model": model,
            "reasoning_effort": reasoning_effort,
            **_usage_dict(usage),
        }
        target = self._log_dir / "usage.jsonl"
        try:
            with target.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")
        except OSError as exc:
            log_and_print(
                f"[usage] failed to append {target}: {type(exc).__name__}: {exc}",
                self._run_log_file,
            )

    def run(
        self,
        *,
        session_spec: AgentSessionSpec,
        turn: AgentTurnRequest,
        session_key: str | None = None,
        observer: AgentObserver | None = None,
    ) -> AgentTurnResult:
        """Run one raw turn, optionally retaining its session for reuse."""
        self._ensure_open()
        if session_key is None:
            return self._run_ephemeral(session_spec, turn, observer)

        cached = self._sessions.get(session_key)
        if cached is None or cached.spec != session_spec:
            if cached is not None:
                self._evict(session_key)
            cached = _CachedSession(
                spec=session_spec,
                session=self._create_session(session_spec),
            )
            self._sessions[session_key] = cached

        try:
            result = cached.session.run_turn(turn, observer)
        except BaseException as error:
            try:
                self._evict(session_key)
            except Exception as cleanup_error:  # noqa: BLE001  # preserve the turn failure
                error.add_note(f"agent session cleanup also failed: {cleanup_error}")
            raise

        if result.disposition is SessionDisposition.RESET_REQUIRED:
            self._evict(session_key)
        return result

    def close(self) -> None:
        """Close all cached sessions and the driver exactly once."""
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        for key in tuple(self._sessions):
            try:
                self._evict(key)
            except Exception as error:  # noqa: BLE001  # cleanup must continue
                if first_error is None:
                    first_error = error
        try:
            self._driver.close()
        except Exception as error:  # noqa: BLE001  # preserve earlier cleanup failures
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    def _run_ephemeral(
        self,
        session_spec: AgentSessionSpec,
        turn: AgentTurnRequest,
        observer: AgentObserver | None,
    ) -> AgentTurnResult:
        session = self._create_session(session_spec)
        turn_error: BaseException | None = None
        try:
            return session.run_turn(turn, observer)
        except BaseException as error:
            turn_error = error
            raise
        finally:
            try:
                session.close()
            except Exception as cleanup_error:
                if turn_error is None:
                    raise
                turn_error.add_note(f"agent session cleanup also failed: {cleanup_error}")

    def _evict(self, key: str) -> None:
        cached = self._sessions.pop(key, None)
        if cached is not None:
            cached.session.close()

    def _create_session(self, spec: AgentSessionSpec) -> AgentSession:
        """Perform VibeSys-owned setup, then delegate runtime-specific setup."""
        materialize_skills(
            spec.workspace,
            list(spec.skills),
            compute_backend=self._compute_backend,
            log_file=self._run_log_file,
        )
        return self._driver.create_session(spec)

    def _ensure_open(self) -> None:
        if self._closed:
            msg = "agent client is closed"
            raise RuntimeError(msg)

    def __enter__(self) -> AgentClient:
        """Return this open client as a context manager."""
        self._ensure_open()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close the client when leaving its context."""
        self.close()
