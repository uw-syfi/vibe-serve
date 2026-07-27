"""Omnigent-backed implementation of :class:`~vibesys.agents.base.AgentRunner`.

This is the opt-in alternative to :class:`~vibesys.agents.cli_runner.CliAgentRunner`,
selected only when :attr:`~vibesys.features.FeatureFlag.OMNIGENT_AGENT_BACKEND`
is enabled. With the flag off — the default — nothing here is imported and the
agentshim path runs unchanged.

Where agentshim exposes ``CLIGenerationSession.generate(prompt, cwd=...)``,
Omnigent exposes ``Executor.run_turn(messages, tools, system_prompt, config)``
as an async event stream. This module adapts the latter to the former's shape
so both backends produce the same run-log output, the same ``usage.jsonl``
records, and the same parsed Pydantic responses.

The entire Omnigent contact surface is this one module — the "isolate hard"
boundary the evaluation spike established. ``omnigent`` is imported lazily
inside :meth:`OmnigentAgentRunner._executor_class` and
:func:`_drive_turn` so that:

- VibeSys keeps working on Python 3.11, where ``omnigent`` (which requires
  3.12+) cannot be installed at all; and
- a missing optional dependency surfaces as an actionable error at the moment
  the flag is used, not as an import failure at process start.

Skill materialization, the JSON schema hint, and response parsing are reused
from the agentshim runner rather than reimplemented, so a prompt-shape change
lands on both backends at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, TextIO, TypeVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from vibesys._agent_cli.base import MCPServerSpec
from vibesys.agent_runner import (
    log_and_print,
    log_json_and_print,
    log_markdown_and_print,
    log_prompt_markdown_and_print,
    parse_typed_response_text,
)
from vibesys.agents.callbacks import AgentLogger
from vibesys.agents.cli_common import (
    agent_label,
    build_schema_hint,
    materialize_skills,
)
from vibesys.agents.omnigent.providers import (
    OMNIGENT_PROVIDER_EXECUTORS,
    OmnigentExecutorSpec,
    supported_providers,
)
from vibesys.agents.progress import AgentProgress

T = TypeVar("T", bound=BaseModel)


class OmnigentUnavailableError(RuntimeError):
    """Raised when the Omnigent backend is requested but cannot be used.

    Carries an actionable message naming the flag and the remedy. The caller
    is expected to let this propagate rather than silently falling back to
    agentshim — an unannounced backend switch would make run logs lie about
    which stack produced a result.
    """


def resolve_executor_spec(provider: str) -> OmnigentExecutorSpec:
    """Return the Omnigent executor spec for *provider*.

    Args:
        provider: A VibeSys cli provider name (``"claude"``, ``"codex"``,
            ``"gemini"``, ``"opencode"``).

    Returns:
        The matching :class:`OmnigentExecutorSpec`.

    Raises:
        OmnigentUnavailableError: If Omnigent has no headless executor for
            *provider*. The message names the supported set so the operator
            can either switch provider or turn the flag back off.
    """
    spec = OMNIGENT_PROVIDER_EXECUTORS.get(provider)
    if spec is None:
        raise OmnigentUnavailableError(
            f"feature flag 'omnigent_agent_backend' is enabled but cli provider "
            f"{provider!r} has no Omnigent executor; supported: "
            f"{supported_providers()}. Disable the flag to use the agentshim "
            f"backend, which supports this provider."
        )
    return spec


def _sandbox_backend_for_platform() -> str:
    """Return the Omnigent sandbox backend identifier for this host.

    Mirrors Omnigent's own platform mapping. Kept in VibeSys source rather than
    delegated to Omnigent's private helper so the confinement choice stays
    auditable here and survives churn in an alpha dependency's internals.

    Raises:
        OmnigentUnavailableError: On a platform with no sandbox backend.
            Running the agent unconfined is deliberately not a fallback.
    """
    if sys.platform.startswith("linux"):
        return "linux_bwrap"
    if sys.platform == "darwin":
        return "darwin_seatbelt"
    if os.name == "nt":
        return "windows_jobobject"
    raise OmnigentUnavailableError(
        f"no Omnigent sandbox backend is available on platform {sys.platform!r}, "
        "and the Omnigent backend will not run an agent unconfined. Disable "
        "'omnigent_agent_backend' to use the agentshim backend."
    )


@contextlib.contextmanager
def _patched_environ(overrides: dict[str, str] | None) -> Generator[None]:
    """Apply *overrides* to ``os.environ`` for the duration of the block.

    Omnigent's executors spawn their harness subprocess from the caller's
    ``os.environ`` rather than accepting an explicit env mapping, so
    per-invocation variables (notably ``CUDA_VISIBLE_DEVICES``) have to be
    staged on the parent process. Restores the previous values — including
    removing keys that were absent — on every exit path.
    """
    if not overrides:
        yield
        return

    sentinel = object()
    previous: dict[str, object] = {k: os.environ.get(k, sentinel) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is sentinel:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(old)


def _flatten_tool_schema(tool: Any) -> dict[str, Any]:
    """Convert a ``Tool.get_schema()`` payload into executor ``ToolSpec`` shape.

    ``get_schema()`` returns OpenAI-function shape
    (``{"type": "function", "function": {"name", "description", "parameters"}}``),
    but ``run_turn`` reads ``schema.get("name")`` / ``("description")`` /
    ``("parameters")`` off the top level (claude_sdk_executor.py:735-743).
    Passing the nested form registers MCP tools with empty names, which the
    agent then cannot see.
    """
    function = tool.get_schema().get("function", {})
    return {
        "name": function.get("name"),
        "description": function.get("description"),
        "parameters": function.get("parameters", {"type": "object", "properties": {}}),
    }


def _build_os_tools(os_env_spec: Any, workspace: Path) -> tuple[list[dict[str, Any]], Any]:
    """Build Omnigent's ``sys_os_*`` tools and a dispatcher for them.

    Passing ``os_env`` to an executor confines the agent, but it does **not**
    give it filesystem access: Omnigent routes file and shell operations
    through ``sys_os_read`` / ``sys_os_write`` / ``sys_os_edit`` /
    ``sys_os_shell`` MCP tools, and the executor expects its caller to supply
    both the schemas and a dispatcher. Omnigent's own scaffold does this in
    ``ToolManager._register_os_env_tools``; VibeSys drives the executor
    directly, so it has to do the same.

    Without this the agent starts with no file tools at all and answers
    "I don't have a file-reading tool available" — verified live before this
    was wired in.

    Returns:
        ``(schemas, dispatch)`` where *schemas* goes to ``run_turn``'s
        ``tools`` argument and *dispatch* is the async ``(name, args)``
        callable the executor invokes per tool call.

    Raises:
        OmnigentUnavailableError: If Omnigent cannot resolve an OS environment
            for the spec, which would otherwise yield a silently toolless agent.
    """
    from omnigent.inner.os_env import create_os_environment
    from omnigent.tools.base import ToolContext
    from omnigent.tools.builtins.os_env import build_os_env_tools

    os_env = create_os_environment(os_env_spec)
    if os_env is None:
        raise OmnigentUnavailableError(
            "Omnigent could not resolve an OS environment for workspace "
            f"{workspace}; the agent would start with no file or shell tools. "
            "Disable 'omnigent_agent_backend' to use the agentshim backend."
        )

    tools = build_os_env_tools(os_env)
    by_name = {tool.name(): tool for tool in tools}
    schemas = [_flatten_tool_schema(tool) for tool in tools]
    context = ToolContext(task_id="vibesys", agent_id="vibesys", workspace=workspace)

    async def dispatch(name: str, args: dict[str, Any]) -> Any:
        tool = by_name.get(name)
        if tool is None:
            return {"error": f"unknown tool {name!r}"}
        # ``Tool.invoke`` is sync and calls ``asyncio.run`` internally, so it
        # must not execute on this thread's running loop.
        return await asyncio.to_thread(tool.invoke, json.dumps(args), context)

    return schemas, dispatch


async def _drive_turn(
    executor: Any,
    *,
    prompt: str,
    system_prompt: str,
    logger: AgentLogger,
    tool_schemas: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Consume one ``run_turn`` event stream into ``(text, usage)``.

    Mirrors the agentshim event handler contract: text deltas, tool calls, and
    tool results are pushed onto *logger* as they arrive so the terminal output
    matches the agentshim backend.

    ``TurnComplete.response`` is authoritative for the final text when present;
    the concatenated :class:`TextChunk` stream is the fallback for executors
    that stream without repeating the full response at the end.
    """
    from omnigent import (
        TextChunk,
        ToolCallComplete,
        ToolCallRequest,
        TurnComplete,
    )

    chunks: list[str] = []
    response: str | None = None
    usage: dict[str, Any] = {}

    # ``run_turn`` is annotated ``list[Message]``, but on 0.6.0 both the
    # Claude and Codex executors consume the list as plain dicts —
    # ``msg.get("role")`` / ``msg.get("content")`` / ``msg.get("session_id")``
    # (claude_sdk_executor.py:1831,2857; codex_executor.py:942,968). Passing
    # the advertised ``Message`` dataclass raises
    # ``AttributeError: 'Message' object has no attribute 'get'`` on the first
    # turn. Dicts are what actually works, so dicts are what we send.
    messages: list[Any] = [{"role": "user", "content": prompt}]
    async for event in executor.run_turn(messages, tool_schemas or [], system_prompt):
        if isinstance(event, TextChunk):
            chunks.append(event.text)
            logger.log_text(event.text)
        elif isinstance(event, ToolCallRequest):
            logger.on_tool_call(event.name, event.args)
        elif isinstance(event, ToolCallComplete):
            logger.on_tool_result(
                event.name,
                event.error if event.error is not None else event.result,
            )
        elif isinstance(event, TurnComplete):
            response = event.response
            usage = event.usage or {}

    text = response if response is not None else "".join(chunks)
    if usage:
        logger.update_usage(usage)
    return text, usage


class OmnigentAgentRunner:
    """:class:`AgentRunner` backed by Omnigent's in-process executors.

    Constructed only by :func:`vibesys.agents.build_agent_runner` when the
    ``omnigent_agent_backend`` flag is on. Construction validates the provider
    eagerly so an unsupported combination fails before the loop starts rather
    than mid-round.
    """

    backend_name = "omnigent"

    def __init__(
        self,
        *,
        provider: str,
        model: str | None = None,
        skills: list[Path] | None = None,
        model_name: str | None = None,
        timeout: int | None = None,
        run_log_file: TextIO | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self._spec = resolve_executor_spec(provider)
        self._provider = provider
        self._model = model
        self._skills: list[Path] = list(skills or [])
        self._model_name = model_name
        self._timeout = timeout
        self._run_log_file = run_log_file
        self._log_dir = log_dir
        # Executors are cached per kind to mirror the agentshim runner's
        # session reuse. "chat" is excluded there because provider session IDs
        # go stale; the same reasoning applies to a resident harness process.
        self._executors: dict[str, tuple[Any, list[dict[str, Any]]]] = {}
        # One loop for the runner's whole life. A cached executor holds an SDK
        # client and subprocess transports bound to the loop that created them,
        # so a fresh ``asyncio.run`` per turn would strand them on a closed
        # loop — observed live as "Event loop is closed" during teardown.
        self._loop: asyncio.AbstractEventLoop | None = None

    def _run_async(self, coro: Any) -> Any:
        """Drive *coro* on this runner's long-lived event loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            self._loop = loop
        return loop.run_until_complete(coro)

    def close(self) -> None:
        """Release cached executors and the runner's event loop.

        Idempotent, and safe to call after a failed turn. Callers that never
        invoke it leak one loop per runner until process exit, which is the
        same lifetime the agentshim runner's cached agents have.
        """
        loop = self._loop
        for executor, _ in self._executors.values():
            close = getattr(executor, "close", None)
            if close is None:
                continue
            try:
                result = close()
                if asyncio.iscoroutine(result) and loop is not None and not loop.is_closed():
                    loop.run_until_complete(result)
            except Exception as exc:  # noqa: BLE001 — cleanup must not mask the caller's error
                log_and_print(
                    f"[omnigent] executor close failed: {type(exc).__name__}: {exc}",
                    self._run_log_file,
                )
        self._executors.clear()
        if loop is not None and not loop.is_closed():
            # Let the SDK's subprocess transports finish tearing down before
            # the loop goes away. Without the drain their ``__del__`` runs
            # against a closed loop and raises "Event loop is closed" during
            # interpreter shutdown.
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(asyncio.sleep(0))
            except Exception as exc:  # noqa: BLE001 — best-effort drain
                log_and_print(
                    f"[omnigent] loop drain failed: {type(exc).__name__}: {exc}",
                    self._run_log_file,
                )
            loop.close()
        self._loop = None

    def _executor_class(self) -> type[Any]:
        """Import and return the Omnigent executor class for this provider.

        Raises:
            OmnigentUnavailableError: If ``omnigent`` is not installed, or is
                installed but no longer exposes the expected class — the alpha
                dependency's documented failure mode.
        """
        try:
            module = import_module(self._spec.module)
        except ImportError as exc:
            raise OmnigentUnavailableError(
                "feature flag 'omnigent_agent_backend' is enabled but the "
                "'omnigent' package is not importable "
                f"({type(exc).__name__}: {exc}). Install the optional extra "
                "with `uv sync --extra omnigent` on Python 3.12+, or disable "
                "the flag to use the agentshim backend."
            ) from exc

        try:
            return getattr(module, self._spec.class_name)
        except AttributeError as exc:
            raise OmnigentUnavailableError(
                f"omnigent module {self._spec.module!r} has no "
                f"{self._spec.class_name!r}; the installed omnigent version is "
                "incompatible with this integration (expected the 0.6.0 "
                "executor API). Disable 'omnigent_agent_backend' to use the "
                "agentshim backend."
            ) from exc

    def _build_os_env(self, workspace: Path) -> Any:
        """Confine the agent to *workspace* using Omnigent's own sandbox.

        The agentshim host path wraps every agent in a ``vs_sandbox`` host
        sandbox (bubblewrap on Linux, Seatbelt on macOS) so a run cannot read
        or modify sibling runs or unrelated host files (issue #149). Omnigent
        spawns its harness itself and will not accept a ``vs_sandbox`` object,
        so the equivalent guarantee has to be expressed in Omnigent's own
        vocabulary: an ``OSEnvSpec`` whose sandbox grants write access to the
        workspace and nothing else.

        Two deliberate choices:

        - The backend ``type`` is chosen here by platform rather than left to
          ``OSEnvSandboxSpec``'s dataclass default of ``"linux_bwrap"``, which
          would be wrong on macOS. Omnigent has a private helper for this, but
          a security decision should be readable in VibeSys's own source and
          not silently follow an alpha dependency's internals.
        - ``sandbox`` is never ``None`` and never ``type="none"``. Omnigent
          resolves the backend binary at run time and fails loudly if it is
          missing, which is the behaviour we want — an unsandboxed agent must
          not be the silent fallback.

        Passing ``os_env`` is also what enables Omnigent's built-in OS tools
        (Bash, Read, Edit), so the agent can still do its job inside the
        confinement.

        This is Omnigent's confinement, not ``vs_sandbox``'s. The two have not
        been proven equivalent; see docs/omnigent-evaluation.md.
        """
        from omnigent.inner.datamodel import OSEnvSandboxSpec, OSEnvSpec

        return OSEnvSpec(
            type="caller_process",
            cwd=str(workspace),
            sandbox=OSEnvSandboxSpec(
                type=_sandbox_backend_for_platform(),
                write_paths=[str(workspace)],
            ),
        )

    def _build_executor(self, workspace: Path) -> tuple[Any, list[dict[str, Any]]]:
        """Construct an executor confined to *workspace* with OS tools attached.

        Returns the executor and the tool schemas to hand ``run_turn``. The
        dispatcher is installed on the executor's ``_tool_executor`` slot,
        which is how Omnigent's own ``ExecutorAdapter`` wires it
        (runtime/harnesses/_executor_adapter.py:302). That attribute is
        private, so this is the single most churn-prone line in the
        integration and one more reason the flag ships off by default.
        """
        executor_cls = self._executor_class()
        os_env_spec = self._build_os_env(workspace)
        executor = executor_cls(
            cwd=str(workspace),
            model=self._model,
            os_env=os_env_spec,
        )
        schemas, dispatch = _build_os_tools(os_env_spec, workspace)
        executor._tool_executor = dispatch
        return executor, schemas

    def invoke(
        self,
        *,
        kind: str,
        workspace: Path,
        system_prompt: str,
        env: dict[str, str] | None = None,
        user_prompt: str,
        response_cls: type[T],
        fallback_factory: Callable[[], T],
        round_label: str,
        invocation_id: str | None = None,
        progress: AgentProgress | None = None,
        mcp_servers: list[MCPServerSpec] | None = None,
        tools: list[BaseTool] | None = None,  # noqa: ARG002 — deepagents-only injection point
    ) -> T:
        schema_hint = build_schema_hint(response_cls)
        text = self._generate(
            kind=kind,
            workspace=workspace,
            env=env,
            system_prompt=system_prompt,
            user_prompt=f"{user_prompt}{schema_hint}",
            round_label=round_label,
            invocation_id=invocation_id,
            progress=progress,
            mcp_servers=mcp_servers,
        )
        label = agent_label(kind)
        parsed = parse_typed_response_text(text, response_cls)
        if parsed is None:
            log_and_print(
                f"\n=== {label} ROUND OUTPUT (missing response) ===",
                self._run_log_file,
            )
            log_and_print(
                f"No structured response received from {label.lower()}.",
                self._run_log_file,
            )
            if text:
                log_and_print(
                    f"\n=== {label} ROUND OUTPUT (raw output) ===",
                    self._run_log_file,
                )
                log_markdown_and_print(text, self._run_log_file)
            return fallback_factory()

        log_and_print(f"\n=== {label} ROUND OUTPUT ===", self._run_log_file)
        log_json_and_print(parsed.model_dump_json(indent=2), self._run_log_file)
        return parsed

    def invoke_text(
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
        tools: list[BaseTool] | None = None,  # noqa: ARG002 — deepagents-only
    ) -> str:
        text = self._generate(
            kind=kind,
            workspace=workspace,
            env=env,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            round_label=round_label,
            invocation_id=invocation_id,
            progress=progress,
            mcp_servers=mcp_servers,
        )
        label = agent_label(kind)
        if text:
            log_and_print(f"\n=== {label} ROUND OUTPUT ===", self._run_log_file)
            log_markdown_and_print(text, self._run_log_file)
        else:
            log_and_print(
                f"\n=== {label} ROUND OUTPUT (missing response) ===",
                self._run_log_file,
            )
            log_and_print(f"No response received from {label.lower()}.", self._run_log_file)
        return text

    def _generate(
        self,
        *,
        kind: str,
        workspace: Path,
        env: dict[str, str] | None,
        system_prompt: str,
        user_prompt: str,
        round_label: str,
        invocation_id: str | None,
        progress: AgentProgress | None,
        mcp_servers: list[MCPServerSpec] | None,
    ) -> str:
        """Run one Omnigent turn with the agentshim runner's logging contract."""
        label = agent_label(kind)
        materialize_skills(workspace, self._skills, log_file=self._run_log_file)

        if mcp_servers:
            # Omnigent owns MCP wiring through its own agent spec, which this
            # integration does not construct. Failing loudly beats silently
            # dropping tools the loop believes the agent can reach.
            raise OmnigentUnavailableError(
                f"the Omnigent backend cannot inject MCP servers "
                f"({[s.name for s in mcp_servers]}) for the {label.lower()} agent; "
                "disable 'omnigent_agent_backend' to use the agentshim backend, "
                "which installs them per invocation."
            )

        logger = AgentLogger(
            log_file=self._run_log_file,
            model_name=self._model_name,
            agent_label=label,
            progress=progress,
            agent_kind=kind,
            round_label=round_label,
            invocation_id=invocation_id,
        )

        reuse_executor = kind != "chat"
        entry = self._executors.get(kind) if reuse_executor else None
        if entry is None:
            entry = self._build_executor(workspace)
            if reuse_executor:
                self._executors[kind] = entry
        executor, tool_schemas = entry

        log_and_print(f"\n=== {label} ROUND START: {round_label} ===", self._run_log_file)
        log_and_print(
            f"backend: omnigent, provider: {self._provider} "
            f"(harness: {self._spec.harness}), model: {self._model_name}, "
            f"cwd: {workspace}",
            self._run_log_file,
        )
        log_and_print("--- input ---", self._run_log_file)
        log_prompt_markdown_and_print(f"{system_prompt}\n\n{user_prompt}", self._run_log_file)

        usage: dict[str, Any] = {}
        try:
            with _patched_environ(env):
                text, usage = self._run_async(
                    _drive_turn(
                        executor,
                        prompt=user_prompt,
                        system_prompt=system_prompt,
                        logger=logger,
                        tool_schemas=tool_schemas,
                    )
                )
        except OmnigentUnavailableError:
            raise
        except Exception as exc:
            log_and_print(f"\n=== {label} ROUND ERROR: {round_label} ===", self._run_log_file)
            log_and_print(f"{type(exc).__name__}: {exc}", self._run_log_file)
            raise
        finally:
            # Tokens were spent whether or not the turn succeeded, so the
            # audit record is written on both paths — same rule as the
            # agentshim runner.
            self._write_usage_record(kind=kind, round_label=round_label, usage=usage)
        return text

    def _write_usage_record(self, *, kind: str, round_label: str, usage: dict[str, Any]) -> None:
        """Append one ``usage.jsonl`` record, matching the agentshim schema.

        Keeping the field set identical means downstream cost tooling does not
        need to know which backend produced a run. Omnigent's ``TurnComplete``
        carries no cost or duration, so those columns are ``None`` rather than
        a fabricated zero.
        """
        if self._log_dir is None:
            return
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "kind": kind,
            "round_label": round_label,
            "provider": self._provider,
            "model": self._model_name,
            "input_tokens": usage.get("input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_cost_usd": usage.get("total_cost_usd"),
            "duration_ms": usage.get("duration_ms"),
        }
        target = self._log_dir / "usage.jsonl"
        try:
            with target.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as exc:
            log_and_print(
                f"[usage] failed to append {target}: {type(exc).__name__}: {exc}",
                self._run_log_file,
            )
