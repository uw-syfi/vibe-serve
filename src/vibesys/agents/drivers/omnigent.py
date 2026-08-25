"""Omnigent implementation of the stateful agent-driver contract."""

# ruff: noqa: TRY003

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from importlib import import_module
from typing import TYPE_CHECKING, Any

from vibesys.agents.cli_common import build_schema_hint
from vibesys.agents.contracts import (
    AgentCapabilities,
    AgentEvent,
    AgentEventKind,
    AgentObserver,
    AgentSessionSpec,
    AgentTurnRequest,
    AgentTurnResult,
    AgentUsage,
)
from vibesys.agents.omnigent.providers import (
    OMNIGENT_PROVIDER_EXECUTORS,
    OmnigentExecutorSpec,
    supported_providers,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

_TOOL_EXECUTOR_ATTR = "_tool_executor"
"""Private Omnigent 0.6.0 tool-dispatch seam, guarded before assignment."""


class OmnigentDriverError(RuntimeError):
    """An Omnigent driver requirement could not be satisfied safely."""


def _resolve_executor_spec(provider: str) -> OmnigentExecutorSpec:
    spec = OMNIGENT_PROVIDER_EXECUTORS.get(provider)
    if spec is None:
        raise OmnigentDriverError(
            f"Omnigent does not support agent provider {provider!r}; "
            f"supported providers: {supported_providers()}"
        )
    return spec


def _missing_omnigent(what: str, exc: ImportError) -> OmnigentDriverError:
    return OmnigentDriverError(
        f"{what} is not importable ({type(exc).__name__}: {exc}). "
        "Install the Omnigent optional dependency with `uv sync --extra omnigent`."
    )


def _sandbox_backend_for_platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux_bwrap"
    if sys.platform == "darwin":
        return "darwin_seatbelt"
    if os.name == "nt":
        return "windows_jobobject"
    raise OmnigentDriverError(f"Omnigent has no sandbox backend for platform {sys.platform!r}")


@contextlib.contextmanager
def _patched_environ(overrides: dict[str, str]) -> Generator[None]:
    """Temporarily expose session environment values to Omnigent subprocesses."""
    sentinel = object()
    previous: dict[str, object] = {key: os.environ.get(key, sentinel) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is sentinel:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(old)


def _flatten_tool_schema(tool: Any) -> dict[str, Any]:  # noqa: ANN401
    function = tool.get_schema().get("function", {})
    return {
        "name": function.get("name"),
        "description": function.get("description"),
        "parameters": function.get("parameters", {"type": "object", "properties": {}}),
    }


def _build_os_tools(
    os_env_spec: Any,  # noqa: ANN401
    workspace: Path,
) -> tuple[list[dict[str, Any]], Callable[[str, dict[str, Any]], Any]]:
    """Build Omnigent's sandboxed filesystem tools and their dispatcher."""
    try:
        from omnigent.inner.os_env import create_os_environment  # noqa: PLC0415
        from omnigent.tools.base import ToolContext  # noqa: PLC0415
        from omnigent.tools.builtins.os_env import build_os_env_tools  # noqa: PLC0415
    except ImportError as exc:
        raise _missing_omnigent("Omnigent OS-environment tools", exc) from exc

    try:
        os_env = create_os_environment(os_env_spec)
    except OSError as exc:
        raise OmnigentDriverError(
            f"Omnigent cannot provide its {_sandbox_backend_for_platform()!r} "
            f"sandbox on this host: {exc}"
        ) from exc
    if os_env is None:
        raise OmnigentDriverError(
            f"Omnigent could not create a sandboxed OS environment for {workspace}"
        )

    tools = build_os_env_tools(os_env)
    by_name = {tool.name(): tool for tool in tools}
    schemas = [_flatten_tool_schema(tool) for tool in tools]
    context = ToolContext(task_id="vibesys", agent_id="vibesys", workspace=workspace)

    async def dispatch(name: str, args: dict[str, Any]) -> Any:  # noqa: ANN401
        tool = by_name.get(name)
        if tool is None:
            return {"error": f"unknown tool {name!r}"}
        return await asyncio.to_thread(tool.invoke, json.dumps(args), context)

    return schemas, dispatch


def _usage_from_mapping(usage: dict[str, Any]) -> AgentUsage:
    return AgentUsage(
        input_tokens=usage.get("input_tokens"),
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
        cache_read_input_tokens=usage.get("cache_read_input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_cost_usd=usage.get("total_cost_usd"),
        duration_ms=usage.get("duration_ms"),
    )


def _emit(observer: AgentObserver | None, event: AgentEvent) -> None:
    if observer is not None:
        observer.on_event(event)


async def _drive_turn(
    executor: Any,  # noqa: ANN401
    *,
    request: AgentTurnRequest,
    reasoning_effort: str | None,
    tool_schemas: list[dict[str, Any]],
    observer: AgentObserver | None,
) -> AgentTurnResult:
    """Translate one Omnigent event stream into neutral events and a result."""
    try:
        from omnigent import (  # noqa: PLC0415
            ExecutorConfig,
            TextChunk,
            ToolCallComplete,
            ToolCallRequest,
            TurnComplete,
        )
    except ImportError as exc:
        raise _missing_omnigent("Omnigent executor event types", exc) from exc

    message = request.message
    if request.output_schema is not None:
        message += build_schema_hint(request.output_schema)
    messages: list[Any] = [{"role": "user", "content": message}]
    config = (
        ExecutorConfig(extra={"reasoning_effort": reasoning_effort})
        if reasoning_effort is not None
        else None
    )
    chunks: list[str] = []
    response: str | None = None
    usage = AgentUsage()

    async for event in executor.run_turn(messages, tool_schemas, request.instructions, config):
        if isinstance(event, TextChunk):
            chunks.append(event.text)
            _emit(observer, AgentEvent(AgentEventKind.TEXT, text=event.text))
        elif isinstance(event, ToolCallRequest):
            _emit(
                observer,
                AgentEvent(
                    AgentEventKind.TOOL_CALL,
                    payload={"tool": event.name, "args": event.args},
                ),
            )
        elif isinstance(event, ToolCallComplete):
            _emit(
                observer,
                AgentEvent(
                    AgentEventKind.TOOL_RESULT,
                    payload={
                        "tool": event.name,
                        "stdout": str(event.result) if event.result is not None else "",
                        "stderr": str(event.error) if event.error is not None else "",
                        "exit_code": None,
                        "duration": event.duration_ms / 1000,
                        "status": getattr(event.status, "value", str(event.status)),
                    },
                ),
            )
        elif isinstance(event, TurnComplete):
            response = event.response
            usage = _usage_from_mapping(event.usage or {})
            if event.usage:
                _emit(observer, AgentEvent(AgentEventKind.USAGE, usage=usage))

    provider_session_id = getattr(executor, "thread_id", None)
    return AgentTurnResult(
        text=response if response is not None else "".join(chunks),
        usage=usage,
        provider_session_id=(provider_session_id if isinstance(provider_session_id, str) else None),
    )


class OmnigentSession:
    """One configured Omnigent executor and provider conversation."""

    def __init__(
        self,
        *,
        driver: OmnigentDriver,
        spec: AgentSessionSpec,
        executor: Any,  # noqa: ANN401
        tool_schemas: list[dict[str, Any]],
    ) -> None:
        """Own ``executor`` until this session is closed."""
        self._driver = driver
        self._spec = spec
        self._executor = executor
        self._tool_schemas = tool_schemas
        self._closed = False
        self._failed = False

    def run_turn(
        self,
        request: AgentTurnRequest,
        observer: AgentObserver | None = None,
    ) -> AgentTurnResult:
        """Run one resumable Omnigent turn."""
        if self._closed:
            raise RuntimeError("Omnigent session is closed")
        if self._failed:
            raise RuntimeError("Omnigent session must be reset after a failed turn")

        turn = _drive_turn(
            self._executor,
            request=request,
            reasoning_effort=self._spec.reasoning_effort,
            tool_schemas=self._tool_schemas,
            observer=observer,
        )
        if request.timeout is not None:
            turn = asyncio.wait_for(turn, timeout=request.timeout.total_seconds())
        try:
            with _patched_environ(dict(self._spec.environment)):
                return self._driver.run_awaitable(turn)
        except BaseException:
            self._failed = True
            raise

    def close(self) -> None:
        """Release the executor exactly once."""
        if self._closed:
            return
        self._closed = True
        self._driver.release_session(self, self._executor)


class OmnigentDriver:
    """Create sandboxed Omnigent sessions and own their async event loop."""

    _CAPABILITIES = AgentCapabilities(
        timeouts=True,
        session_reuse=True,
    )

    def __init__(self) -> None:
        """Create a driver with no live sessions or event loop."""
        self._sessions: set[OmnigentSession] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    @property
    def capabilities(self) -> AgentCapabilities:
        """Describe the requirements Omnigent 0.6.0 can satisfy."""
        return self._CAPABILITIES

    def create_session(self, spec: AgentSessionSpec) -> OmnigentSession:
        """Validate setup requirements and create a confined session."""
        if self._closed:
            raise RuntimeError("Omnigent driver is closed")
        self._validate_spec(spec)
        executor, schemas = self._build_executor(spec)
        session = OmnigentSession(
            driver=self,
            spec=spec,
            executor=executor,
            tool_schemas=schemas,
        )
        self._sessions.add(session)
        return session

    def close(self) -> None:
        """Close every outstanding session and the shared event loop."""
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        for session in tuple(self._sessions):
            try:
                session.close()
            except Exception as exc:  # noqa: BLE001
                if first_error is None:
                    first_error = exc
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(asyncio.sleep(0))
            except Exception as exc:  # noqa: BLE001
                if first_error is None:
                    first_error = exc
            finally:
                loop.close()
        self._loop = None
        if first_error is not None:
            raise first_error

    def _validate_spec(self, spec: AgentSessionSpec) -> None:
        _resolve_executor_spec(spec.provider)
        if spec.mcp_servers:
            names = [server.name for server in spec.mcp_servers]
            raise OmnigentDriverError(f"Omnigent cannot install session MCP servers: {names}")
        if spec.policy.host_resources:
            raise OmnigentDriverError("Omnigent cannot enforce VibeSys host-resource grants")
        if spec.policy.containerized:
            raise OmnigentDriverError(
                "Omnigent cannot run this agent in VibeSys's container execution path"
            )
        project_paths = spec.policy.project_paths
        if project_paths is not None and (
            project_paths.read_only_paths or project_paths.hidden_paths
        ):
            raise OmnigentDriverError(
                "Omnigent 0.6.0 cannot enforce VibeSys nested read-only or hidden paths"
            )

    def run_awaitable(self, awaitable: Any) -> Any:  # noqa: ANN401
        """Run one Omnigent awaitable on the driver-owned event loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            self._loop = loop
        return loop.run_until_complete(awaitable)

    def _executor_class(self, spec: OmnigentExecutorSpec) -> type[Any]:
        try:
            module = import_module(spec.module)
        except ImportError as exc:
            raise _missing_omnigent(spec.module, exc) from exc
        try:
            return getattr(module, spec.class_name)
        except AttributeError as exc:
            raise OmnigentDriverError(
                f"Omnigent module {spec.module!r} has no {spec.class_name!r}; "
                "this integration requires the Omnigent 0.6.0 executor API"
            ) from exc

    def _build_os_env(self, workspace: Path) -> Any:  # noqa: ANN401
        try:
            from omnigent.inner.datamodel import OSEnvSandboxSpec, OSEnvSpec  # noqa: PLC0415
        except ImportError as exc:
            raise _missing_omnigent("Omnigent OS-environment datamodel", exc) from exc
        return OSEnvSpec(
            type="caller_process",
            cwd=str(workspace),
            sandbox=OSEnvSandboxSpec(
                type=_sandbox_backend_for_platform(),
                write_paths=[str(workspace)],
            ),
        )

    def _build_executor(self, spec: AgentSessionSpec) -> tuple[Any, list[dict[str, Any]]]:
        executor_spec = _resolve_executor_spec(spec.provider)
        executor_cls = self._executor_class(executor_spec)
        os_env_spec = self._build_os_env(spec.workspace)
        try:
            executor = executor_cls(
                cwd=str(spec.workspace),
                model=spec.model,
                os_env=os_env_spec,
            )
        except ImportError as exc:
            raise OmnigentDriverError(
                f"Omnigent provider {spec.provider!r} is unavailable: {exc}"
            ) from exc
        if not hasattr(executor, _TOOL_EXECUTOR_ATTR):
            with contextlib.suppress(Exception):
                self.close_executor(executor)
            raise OmnigentDriverError(
                f"{executor_cls.__name__} has no {_TOOL_EXECUTOR_ATTR!r} slot; "
                "this integration requires the private Omnigent 0.6.0 tool-dispatch seam"
            )
        try:
            schemas, dispatch = _build_os_tools(os_env_spec, spec.workspace)
        except BaseException:
            with contextlib.suppress(Exception):
                self.close_executor(executor)
            raise
        setattr(executor, _TOOL_EXECUTOR_ATTR, dispatch)
        return executor, schemas

    def release_session(self, session: OmnigentSession, executor: Any) -> None:  # noqa: ANN401
        """Forget a session and close its native executor."""
        self._sessions.discard(session)
        self.close_executor(executor)

    def close_executor(self, executor: Any) -> None:  # noqa: ANN401
        """Close a native executor, awaiting asynchronous cleanup when needed."""
        close = getattr(executor, "close", None)
        if close is None:
            return
        result = close()
        if asyncio.iscoroutine(result):
            self.run_awaitable(result)
