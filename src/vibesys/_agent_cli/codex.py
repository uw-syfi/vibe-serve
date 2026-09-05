import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentshim.codex import CodexGenerationSession
from agentshim.events import AgentEventHandler

from .base import MCPServerSpec
from .cli_agent import CLICodingAgent

if TYPE_CHECKING:
    from agentshim.executor import CommandExecutor

_COMMAND_TOOL = "execute"


class _PairedCodexToolEvents:
    """Repair the codex adapter's tool-event stream into call/result pairs.

    agentshim through 0.5.1 maps a generic codex item's ``item.started`` and
    ``item.completed`` both to tool-use, so one ``file_change`` (or
    ``web_search``, ``mcp_tool_call``, ...) reaches ``on_tool_call`` twice
    with identical arguments and never reaches ``on_tool_result``. This
    wrapper treats the second identical call for a still-open item as that
    item's completion: it swallows the duplicate and emits the missing
    ``on_tool_result`` instead. Command executions (``execute``) already
    arrive correctly paired and pass through untouched, as does every other
    callback. Under an adapter that reports completions as results, no
    duplicate ever arrives and the wrapper forwards everything unchanged.
    """

    def __init__(self, inner: AgentEventHandler) -> None:
        self._inner = inner
        # One open (args, start time) per tool name: codex items in a turn
        # are sequential, so a second identical call while the first is open
        # can only be the same item's completion echo.
        self._open: dict[str, tuple[Any, float]] = {}

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401  # tracked: #288
        return getattr(self._inner, name)

    def on_tool_call(self, tool: str, args: Any = None) -> None:  # noqa: ANN401  # tracked: #288
        """Forward a tool call, folding a completion echo into its result."""
        if tool != _COMMAND_TOOL:
            opened = self._open.pop(tool, None)
            if opened is not None and opened[0] == args:
                self._inner.on_tool_result(
                    tool=tool,
                    stdout="",
                    exit_code=None,
                    duration=time.monotonic() - opened[1],
                )
                return
            self._open[tool] = (args, time.monotonic())
        self._inner.on_tool_call(tool, args)

    def on_tool_result(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401  # tracked: #288
        """Forward a real result verbatim and close the matching open call."""
        tool = kwargs.get("tool", args[0] if args else None)
        self._open.pop(tool, None)
        self._inner.on_tool_result(*args, **kwargs)


def _toml_str(value: str) -> str:
    """Quote *value* as a TOML basic string literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_array(values: list[str]) -> str:
    """Render a list of strings as a TOML inline array of basic strings."""
    return "[" + ",".join(_toml_str(v) for v in values) + "]"


def _shell_path_config_args(env: dict[str, str]) -> list[str]:
    """Preserve the launcher PATH in commands spawned by Codex."""
    path = env.get("PATH")
    if not path:
        return []
    return ["--config", f"shell_environment_policy.set.PATH={_toml_str(path)}"]


class CodexCodingAgent(CLICodingAgent[CodexGenerationSession]):
    """Coding agent implementation using the Codex CLI tool."""

    supports_native_output_schema = True
    # ``codex exec resume <thread-id>`` continues a stored rollout, so a
    # checkpointed thread ID can be adopted before the next turn.
    supports_session_resume = True

    def __init__(  # noqa: ANN204  # tracked: #288
        self,
        model: str | None = None,
        event_handler: AgentEventHandler | None = None,
        *,
        executor: "CommandExecutor | None" = None,
    ):
        """Initialize the Codex coding agent.

        Args:
            model: Optional model name to use with codex. If None, uses default.
            event_handler: Optional event handler for UI updates.
            executor: Optional agentshim :class:`CommandExecutor`.
        """
        super().__init__(
            "codex",
            model,
            event_handler,
            executor=executor,
        )
        # Extra ``--config key=value`` flags appended to ``codex exec`` by
        # :meth:`_get_command`. Populated by :meth:`install_mcp_servers`
        # because Codex has no project-level config file discovery — its
        # only project-scoped knob is the runtime ``--config`` override
        # layer (verified via codex-rs/core/src/config_loader/README.md).
        self.base_config_args = _shell_path_config_args(self.env)
        self.extra_config_args: list[str] = []
        self.output_schema_path: str | None = None

    def set_reasoning_effort(self, effort: str) -> None:
        """Apply a per-agent Codex reasoning effort to fresh and resumed turns."""
        self.base_config_args.extend(["--config", f"model_reasoning_effort={_toml_str(effort)}"])

    def set_output_schema_path(self, path: str | None) -> None:
        """Apply a native final-response schema to the next Codex turn."""
        self.output_schema_path = path

    def _append_output_schema(self, cmd: list[str]) -> None:
        path = getattr(self, "output_schema_path", None)
        if path:
            cmd.extend(["--output-schema", path])

    @property
    def codex_path(self) -> str:
        """Return path to codex binary (for backward compatibility)."""
        return self.binary_path

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return "[Codex]"

    def _get_command(self, prompt: str) -> list[str]:  # noqa: ARG002  # tracked: #288
        cmd = [
            self.binary_path,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.base_config_args:
            cmd.extend(self.base_config_args)
        if self.extra_config_args:
            cmd.extend(self.extra_config_args)
        self._append_output_schema(cmd)
        return cmd

    def _get_resume_command(self, prompt: str, session_id: str) -> list[str]:  # noqa: ARG002  # tracked: #288
        # ``codex exec resume`` does NOT fall back to stdin when the ``[PROMPT]``
        # positional is omitted — only the literal ``-`` sentinel makes it read
        # from stdin. Pass ``-`` so the prompt we write to the subprocess's
        # stdin is actually consumed. (``codex exec`` is more lenient and
        # treats stdin as the default fallback, so we don't need this there.)
        cmd = [
            self.binary_path,
            "exec",
            "resume",
            session_id,
            "-",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.base_config_args:
            cmd.extend(self.base_config_args)
        if self.extra_config_args:
            cmd.extend(self.extra_config_args)
        self._append_output_schema(cmd)
        return cmd

    def _create_session(
        self,
        cmd: list[str],
        cwd: str | None = None,
        timeout: int | None = None,
        silent: bool = False,  # noqa: FBT001, FBT002  # tracked: #288
    ) -> CodexGenerationSession:
        # A fresh wrapper per session: open-call state must not leak across
        # turns.
        event_handler = (
            _PairedCodexToolEvents(self.event_handler) if self.event_handler is not None else None
        )
        return CodexGenerationSession(
            binary_name=self.binary_name,
            env=self.env,
            log_prefix=self._log_prefix,
            cmd=cmd,
            logger=self.logger,
            cwd=cwd,
            timeout=timeout,
            silent=silent,
            event_handler=event_handler,
            executor=self.executor,
        )

    def _extract_session_id(self, session: CodexGenerationSession) -> str | None:
        return session.session_id

    def install_mcp_servers(self, workspace: Path, servers: list[MCPServerSpec]) -> None:  # noqa: ARG002  # tracked: #288
        """Stash ``--config mcp_servers.<name>.<key>=<value>`` flags on the
        instance for the next ``codex exec`` invocation.

        Codex has no project-scoped config file (its config loader only
        looks at MDM, system-managed config, session ``--config`` flags,
        and ``~/.codex/config.toml``), so MCP servers are configured by
        passing dotted-path TOML overrides at the command line. ``--config``
        values are parsed as TOML literals, so strings need TOML quoting
        and arrays use TOML inline array syntax.

        TOML table keys are snake_case by convention, so ``"vibesys-issues"``
        becomes ``mcp_servers.vibesys_issues``.
        """  # noqa: D205  # tracked: #288
        flags: list[str] = []
        for s in servers:
            key = s.name.replace("-", "_")
            flags.extend(
                [
                    "--config",
                    f"mcp_servers.{key}.command={_toml_str(s.command)}",
                    "--config",
                    f"mcp_servers.{key}.args={_toml_array(list(s.args))}",
                ]
            )
            for env_key, env_val in s.env.items():
                flags.extend(
                    [
                        "--config",
                        f"mcp_servers.{key}.env.{env_key}={_toml_str(env_val)}",
                    ]
                )
        self.extra_config_args = flags

    def uninstall_mcp_servers(self, workspace: Path, servers: list[MCPServerSpec]) -> None:  # noqa: ARG002  # tracked: #288
        """Clear the runtime ``--config`` flags. Idempotent."""
        self.extra_config_args = []
