from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentshim.events import AgentEventHandler
from agentshim.opencode import OpencodeGenerationSession

from .base import MCPServerSpec
from .cli_agent import CLICodingAgent

if TYPE_CHECKING:
    from agentshim.executor import CommandExecutor

OPENCODE_DEFAULT_MODEL = "google-vertex/gemini-3-pro-preview"

# How many of opencode's ``{"type":"error",...}`` stdout events to keep for the
# failure message; the last ones name the actual provider/API failure.
_ERROR_EVENTS_TO_KEEP = 5


class _ErrorCapturingOpencodeSession(OpencodeGenerationSession):
    """Keep opencode's ``error`` events so a failed run says why it failed.

    agentshim's parser handles text, step-finish and tool events; opencode
    reports API and provider failures as ``{"type":"error",...}`` events on
    stdout, which otherwise vanish and leave a bare "exited with code 1".
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401  # tracked: #288
        super().__init__(*args, **kwargs)
        self.raw_error_lines: list[str] = []

    def _process_stdout(self, line: str) -> None:
        if '"type":"error"' in line:
            self.raw_error_lines.append(line.rstrip("\n"))
            del self.raw_error_lines[:-_ERROR_EVENTS_TO_KEEP]
        super()._process_stdout(line)

    def run(self, prompt: str) -> str:
        """Run the generation; append captured error events to a failure."""
        try:
            return super().run(prompt)
        except RuntimeError as exc:
            if not self.raw_error_lines:
                raise
            detail = "\n".join(self.raw_error_lines)
            raise RuntimeError(f"{exc}\nopencode error events:\n{detail}") from exc  # noqa: TRY003  # tracked: #288


class OpencodeCodingAgent(CLICodingAgent[OpencodeGenerationSession]):
    """Coding agent implementation using the Opencode CLI tool."""

    def __init__(  # noqa: ANN204  # tracked: #288
        self,
        model: str | None = None,
        event_handler: AgentEventHandler | None = None,
        *,
        executor: "CommandExecutor | None" = None,
    ):
        """Initialize the Opencode coding agent.

        Args:
            model: Optional model name to use.
            event_handler: Optional event handler for UI updates.
            executor: Optional agentshim :class:`CommandExecutor`.
        """
        if not model:
            model = OPENCODE_DEFAULT_MODEL
        super().__init__(
            "opencode",
            model,
            event_handler,
            executor=executor,
        )

    @property
    def _log_prefix(self) -> str:
        """Return the log prefix for this agent."""
        return "[Opencode]"

    def _base_command(self) -> list[str]:
        # The prompt reaches opencode on stdin (``CLIGenerationSession.run``
        # writes it there). Passing it as a positional as well would send it
        # twice and, for long orchestrator prompts, exceed the OS argv limit.
        cmd = [self.binary_path, "run"]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.append("--format=json")
        return cmd

    def _get_command(self, prompt: str) -> list[str]:  # noqa: ARG002  # tracked: #288
        return self._base_command()

    def _get_resume_command(self, prompt: str, session_id: str) -> list[str]:  # noqa: ARG002  # tracked: #288
        # Continue the SAME opencode session so follow-ups (retry feedback, a
        # missing-response nudge) keep the conversation context. Without this
        # every follow-up lands in a fresh, empty session.
        cmd = self._base_command()
        cmd[2:2] = ["--session", session_id]
        return cmd

    def _extract_session_id(self, session: OpencodeGenerationSession) -> str | None:
        # ``OpencodeGenerationSession`` captures ``sessionID`` from the JSON
        # event stream while parsing stdout.
        return session.session_id

    def _create_session(
        self,
        cmd: list[str],
        cwd: str | None = None,
        timeout: int | None = None,
        silent: bool = False,  # noqa: FBT001, FBT002  # tracked: #288
    ) -> OpencodeGenerationSession:
        return _ErrorCapturingOpencodeSession(
            binary_name=self.binary_name,
            env=self.env,
            log_prefix=self._log_prefix,
            cmd=cmd,
            logger=self.logger,
            cwd=cwd,
            timeout=timeout,
            silent=silent,
            event_handler=self.event_handler,
            executor=self.executor,
        )

    def install_mcp_servers(self, workspace: Path, servers: list[MCPServerSpec]) -> None:
        """Merge servers into ``<workspace>/opencode.json``.

        Opencode discovers the MCP servers from cwd and uses the ``mcp`` key (not
        ``mcpServers``) and a single combined ``command`` array. Non-
        interactive ``opencode run`` already auto-approves all permissions,
        so no extra ``permission`` block is needed.
        """
        server_config: dict[str, dict[str, Any]] = {
            s.name: {
                "type": "local",
                "command": [s.command, *s.args],
                "enabled": True,
                **({"environment": dict(s.env)} if s.env else {}),
            }
            for s in servers
        }
        self._install_mcp_config_file(
            workspace / "opencode.json",
            server_key="mcp",
            server_config=server_config,
            defaults={"$schema": "https://opencode.ai/config.json"},
        )

    def uninstall_mcp_servers(self, workspace: Path, servers: list[MCPServerSpec]) -> None:  # noqa: ARG002  # tracked: #288
        """Restore the workspace's original ``opencode.json``. Idempotent."""
        self._restore_mcp_config_file(workspace / "opencode.json")
