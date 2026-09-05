"""Unit tests for the Codex provider's command construction and JSONL parser.

These tests exercise :mod:`vibesys._agent_cli.codex` without spawning the real
``codex`` binary. They cover the three correctness fixes:

1. ``_get_command`` / ``_get_resume_command`` include ``--skip-git-repo-check``
   so codex doesn't refuse to run outside a git repo.
2. ``_get_resume_command`` passes ``-`` as the prompt positional so the stdin
   write in :class:`CLIGenerationSession.run` is actually consumed.
3. ``CodexGenerationSession`` captures cumulative token usage from
   ``turn.completed`` events and forwards a ``reasoning`` item's text through
   the event handler.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from vibesys._agent_cli.codex import (
    CodexCodingAgent,
    CodexGenerationSession,
    _PairedCodexToolEvents,
    _shell_path_config_args,
)
from vibesys._agent_cli.gemini import GeminiCodingAgent


def _agent() -> CodexCodingAgent:
    """Build a CodexCodingAgent without running binary detection."""
    agent = CodexCodingAgent.__new__(CodexCodingAgent)
    agent.binary_path = "/usr/local/bin/codex"
    agent.model = None
    agent.base_config_args = []
    agent.extra_config_args = []
    agent.output_schema_path = None
    return agent


def _session(event_handler=None) -> CodexGenerationSession:  # noqa: ANN001  # tracked: #288
    """Build a CodexGenerationSession without opening pipes."""
    return CodexGenerationSession(
        binary_name="codex",
        env={},
        log_prefix="[Codex]",
        cmd=["codex", "exec", "--json", "-"],
        logger=MagicMock(),
        silent=True,
        event_handler=event_handler,
    )


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


class TestGetCommand:
    def test_shell_path_config_preserves_launcher_path(self):  # noqa: ANN202  # tracked: #288
        assert _shell_path_config_args({"PATH": '/opt/go/bin:/path/with"quote'}) == [
            "--config",
            'shell_environment_policy.set.PATH="/opt/go/bin:/path/with\\"quote"',
        ]

    def test_shell_path_config_omits_missing_path(self):  # noqa: ANN202  # tracked: #288
        assert _shell_path_config_args({}) == []

    def test_initial_command_includes_skip_git_repo_check(self):  # noqa: ANN202  # tracked: #288
        cmd = _agent()._get_command("hello")  # noqa: SLF001  # tracked: #288
        assert "--skip-git-repo-check" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--json" in cmd
        # The subcommand must be ``exec`` (not ``exec resume``).
        assert cmd[1] == "exec"
        assert "resume" not in cmd

    def test_initial_command_includes_model_when_set(self):  # noqa: ANN202  # tracked: #288
        agent = _agent()
        agent.model = "gpt-5"
        cmd = agent._get_command("hello")  # noqa: SLF001  # tracked: #288
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "gpt-5"

    def test_initial_command_appends_extra_config_args(self):  # noqa: ANN202  # tracked: #288
        agent = _agent()
        agent.extra_config_args = ["--config", "foo=1"]
        cmd = agent._get_command("hello")  # noqa: SLF001  # tracked: #288
        assert cmd[-2:] == ["--config", "foo=1"]

    def test_reasoning_effort_is_passed_as_codex_config(self):  # noqa: ANN202  # tracked: #288
        agent = _agent()
        agent.set_reasoning_effort("xhigh")
        cmd = agent._get_command("hello")  # noqa: SLF001  # tracked: #288
        assert 'model_reasoning_effort="xhigh"' in cmd

    def test_initial_command_includes_native_output_schema(self):  # noqa: ANN202  # tracked: #288
        agent = _agent()
        agent.set_output_schema_path(".cache/vibesys/response-schemas/judge.json")

        cmd = agent._get_command("hello")  # noqa: SLF001  # tracked: #288

        assert cmd[cmd.index("--output-schema") + 1] == (
            ".cache/vibesys/response-schemas/judge.json"
        )


class TestGetResumeCommand:
    def test_resume_passes_dash_positional(self):  # noqa: ANN202  # tracked: #288
        """Without ``-``, codex exec resume silently ignores stdin."""
        cmd = _agent()._get_resume_command("prompt", "sess-123")  # noqa: SLF001  # tracked: #288
        # The positional args come right after the subcommand path:
        #   codex exec resume <session_id> <prompt>
        assert cmd[:5] == [
            "/usr/local/bin/codex",
            "exec",
            "resume",
            "sess-123",
            "-",
        ]

    def test_resume_command_includes_skip_git_repo_check(self):  # noqa: ANN202  # tracked: #288
        cmd = _agent()._get_resume_command("prompt", "sess-123")  # noqa: SLF001  # tracked: #288
        assert "--skip-git-repo-check" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--json" in cmd

    def test_resume_command_passes_model_and_extra_config(self):  # noqa: ANN202  # tracked: #288
        agent = _agent()
        agent.model = "gpt-5"
        agent.extra_config_args = ["--config", 'mcp_servers.x.command="python"']
        cmd = agent._get_resume_command("prompt", "sess-123")  # noqa: SLF001  # tracked: #288
        assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "gpt-5"  # noqa: PT018  # tracked: #288
        assert cmd[-2:] == ["--config", 'mcp_servers.x.command="python"']

    def test_resume_command_includes_native_output_schema(self):  # noqa: ANN202  # tracked: #288
        agent = _agent()
        agent.set_output_schema_path(".cache/vibesys/response-schemas/implementer.json")

        cmd = agent._get_resume_command("prompt", "sess-123")  # noqa: SLF001  # tracked: #288

        assert cmd[cmd.index("--output-schema") + 1] == (
            ".cache/vibesys/response-schemas/implementer.json"
        )


# ---------------------------------------------------------------------------
# Stream parser
# ---------------------------------------------------------------------------


class TestProcessStdout:
    def test_thread_started_captures_thread_id(self):  # noqa: ANN202  # tracked: #288
        session = _session()
        session._process_stdout(json.dumps({"type": "thread.started", "thread_id": "t-1"}))  # noqa: SLF001  # tracked: #288
        assert session.session_id == "t-1"

    def test_agent_message_captures_last_text(self):  # noqa: ANN202  # tracked: #288
        session = _session()
        session._process_stdout(  # noqa: SLF001  # tracked: #288
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "final answer"},
                }
            )
        )
        assert session.final_result == "final answer"

    def test_agent_message_streams_through_on_thinking(self):  # noqa: ANN202  # tracked: #288
        """Assistant text should land in the log as soon as it arrives."""
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout(  # noqa: SLF001  # tracked: #288
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "final answer"},
                }
            )
        )
        handler.on_thinking.assert_called_once_with("final answer")

    def test_multiple_agent_messages_keep_last(self):  # noqa: ANN202  # tracked: #288
        session = _session()
        for text in ["first", "second", "third"]:
            session._process_stdout(  # noqa: SLF001  # tracked: #288
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": text},
                    }
                )
            )
        assert session.final_result == "third"

    def test_reasoning_forwards_to_on_thinking(self):  # noqa: ANN202  # tracked: #288
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout(  # noqa: SLF001  # tracked: #288
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "reasoning", "text": "I should grep for X"},
                }
            )
        )
        handler.on_thinking.assert_called_once_with("I should grep for X")

    def test_unknown_item_types_fall_back_to_tool_call(self):  # noqa: ANN202  # tracked: #288
        """file_change / mcp_tool_call / todo_list / web_search / error …
        all surface through ``on_tool_call`` so their payloads land in the log."""
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout(  # noqa: SLF001  # tracked: #288
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "file_change",
                        "path": "engine.py",
                        "kind": "update",
                    },
                }
            )
        )
        handler.on_tool_call.assert_called_once_with(
            "file_change", {"path": "engine.py", "kind": "update"}
        )

    def test_mcp_tool_call_falls_back_with_full_args(self):  # noqa: ANN202  # tracked: #288
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout(  # noqa: SLF001  # tracked: #288
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "server": "vibesys_issues",
                        "tool": "list_issues",
                        "arguments": {"cap": 1},
                        "result": "[]",
                    },
                }
            )
        )
        handler.on_tool_call.assert_called_once_with(
            "mcp_tool_call",
            {
                "server": "vibesys_issues",
                "tool": "list_issues",
                "arguments": {"cap": 1},
                "result": "[]",
            },
        )

    def test_error_item_surfaces_message(self):  # noqa: ANN202  # tracked: #288
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout(  # noqa: SLF001  # tracked: #288
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "error", "message": "rate limited"},
                }
            )
        )
        handler.on_tool_call.assert_called_once_with("error", {"message": "rate limited"})

    def test_command_execution_forwards_tool_call_and_result(self):  # noqa: ANN202  # tracked: #288
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout(  # noqa: SLF001  # tracked: #288
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "ls -la",
                        "aggregated_output": "file.txt\n",
                    },
                }
            )
        )
        handler.on_tool_call.assert_called_once_with("execute", {"command": "ls -la"})
        handler.on_tool_result.assert_called_once_with(
            tool="execute", stdout="file.txt\n", exit_code=None, duration=None
        )

    def test_turn_completed_captures_usage_and_normalizes_fields(self):  # noqa: ANN202  # tracked: #288
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout(  # noqa: SLF001  # tracked: #288
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 1200,
                        "cached_input_tokens": 800,
                        "output_tokens": 150,
                    },
                }
            )
        )
        assert session.final_usage == {
            "input_tokens": 1200,
            "output_tokens": 150,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 0,
        }
        handler.on_usage.assert_called_once_with(session.final_usage)
        # Also emits a visible marker line so a tail on the log shows the
        # turn completing, not just a silent usage update.
        thinking_calls = [c.args[0] for c in handler.on_thinking.call_args_list]
        assert any("turn complete" in t for t in thinking_calls)

    def test_turn_completed_without_usage_leaves_none(self):  # noqa: ANN202  # tracked: #288
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout(json.dumps({"type": "turn.completed"}))  # noqa: SLF001  # tracked: #288
        assert session.final_usage is None
        # Still emits a marker so the log shows the turn boundary.
        handler.on_thinking.assert_called_once_with("[codex turn complete]")

    def test_thread_started_emits_marker(self):  # noqa: ANN202  # tracked: #288
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout(json.dumps({"type": "thread.started", "thread_id": "t-1"}))  # noqa: SLF001  # tracked: #288
        assert session.session_id == "t-1"
        handler.on_thinking.assert_called_once_with("[codex thread t-1 started]")

    def test_turn_started_emits_marker(self):  # noqa: ANN202  # tracked: #288
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout(json.dumps({"type": "turn.started"}))  # noqa: SLF001  # tracked: #288
        handler.on_thinking.assert_called_once_with("[codex turn started]")

    def test_unknown_event_types_forward_raw_line(self):  # noqa: ANN202  # tracked: #288
        """item.started / item.updated / future events pass through as thinking
        text so nothing codex emits is silently swallowed."""
        handler = MagicMock()
        session = _session(event_handler=handler)
        raw = json.dumps(
            {"type": "item.updated", "item": {"type": "reasoning", "delta": "thinking..."}}
        )
        session._process_stdout(raw)  # noqa: SLF001  # tracked: #288
        handler.on_thinking.assert_called_once_with(raw)

    def test_non_json_line_forwarded_to_event_handler(self):  # noqa: ANN202  # tracked: #288
        """Codex prints banners/warnings as plain text; these must hit the log
        even when ``silent=True`` (the legacy loguru path is gated on silent)."""
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stdout("starting codex 1.2.3\n")  # noqa: SLF001  # tracked: #288
        handler.on_thinking.assert_called_once_with("starting codex 1.2.3")
        assert "starting codex 1.2.3" in session.stdout_lines[0]

    def test_non_json_line_is_still_recorded(self):  # noqa: ANN202  # tracked: #288
        session = _session()
        session._process_stdout("not json at all\n")  # noqa: SLF001  # tracked: #288
        assert "not json at all" in session.stdout_lines[0]

    def test_blank_line_is_ignored(self):  # noqa: ANN202  # tracked: #288
        session = _session()
        session._process_stdout("   \n")  # noqa: SLF001  # tracked: #288
        assert session.stdout_lines == []


# ---------------------------------------------------------------------------
# Tool-event pairing
# ---------------------------------------------------------------------------


def _agent_session(handler) -> CodexGenerationSession:  # noqa: ANN001  # tracked: #288
    """Build a session through the agent, so the pairing wrapper is wired in."""
    agent = _agent()
    agent.binary_name = "codex"
    agent.env = {}
    agent.logger = MagicMock()
    agent.executor = None
    agent.event_handler = handler
    return agent._create_session(["codex", "exec", "--json", "-"], silent=True)  # noqa: SLF001  # tracked: #288


class TestToolEventPairing:
    def test_generic_item_lifecycle_reaches_handler_as_one_call_and_one_result(self):  # noqa: ANN202  # tracked: #288
        """One codex ``file_change`` item must surface exactly one tool call
        and one tool result, whichever agentshim maps its lifecycle events to
        (agentshim <= 0.5.1 reports ``item.completed`` as a second identical
        tool call and no result)."""
        handler = MagicMock()
        session = _agent_session(handler)
        for event_type, status in [
            ("item.started", "in_progress"),
            ("item.completed", "completed"),
        ]:
            session._process_stdout(  # noqa: SLF001  # tracked: #288
                json.dumps(
                    {
                        "type": event_type,
                        "item": {
                            "id": "fc1",
                            "type": "file_change",
                            "status": status,
                            "path": "engine.py",
                            "kind": "update",
                        },
                    }
                )
            )
        handler.on_tool_call.assert_called_once_with(
            "file_change", {"path": "engine.py", "kind": "update"}
        )
        handler.on_tool_result.assert_called_once()
        result = handler.on_tool_result.call_args.kwargs
        assert result["tool"] == "file_change"
        assert result.get("stdout", "") == ""
        assert result["exit_code"] is None
        assert result["duration"] is not None
        assert result["duration"] >= 0
        # The result must follow the call.
        tool_events = [name for name, *_ in handler.method_calls if name.startswith("on_tool")]
        assert tool_events == ["on_tool_call", "on_tool_result"]

    def test_create_session_without_handler_adds_no_wrapper(self):  # noqa: ANN202  # tracked: #288
        session = _agent_session(None)
        assert not isinstance(session.event_handler, _PairedCodexToolEvents)

    def test_duplicate_call_becomes_the_missing_result(self):  # noqa: ANN202  # tracked: #288
        inner = MagicMock()
        paired = _PairedCodexToolEvents(inner)
        paired.on_tool_call("file_change", {"path": "a.py"})
        paired.on_tool_call("file_change", {"path": "a.py"})
        inner.on_tool_call.assert_called_once_with("file_change", {"path": "a.py"})
        inner.on_tool_result.assert_called_once()
        assert inner.on_tool_result.call_args.kwargs["tool"] == "file_change"
        assert inner.on_tool_result.call_args.kwargs["duration"] >= 0

    def test_consecutive_identical_items_each_get_a_call_and_a_result(self):  # noqa: ANN202  # tracked: #288
        """started/completed, twice over: two items, two calls, two results."""
        inner = MagicMock()
        paired = _PairedCodexToolEvents(inner)
        for _ in range(2):
            paired.on_tool_call("file_change", {"path": "a.py"})
            paired.on_tool_call("file_change", {"path": "a.py"})
        assert inner.on_tool_call.call_count == 2
        assert inner.on_tool_result.call_count == 2

    def test_execute_calls_are_never_folded(self):  # noqa: ANN202  # tracked: #288
        """Command executions already pair correctly upstream; repeating an
        identical command must stay two distinct calls."""
        inner = MagicMock()
        paired = _PairedCodexToolEvents(inner)
        paired.on_tool_call("execute", {"command": "ls"})
        paired.on_tool_call("execute", {"command": "ls"})
        assert inner.on_tool_call.call_count == 2
        inner.on_tool_result.assert_not_called()

    def test_real_result_closes_the_open_call(self):  # noqa: ANN202  # tracked: #288
        """Under a fixed adapter the completion arrives as a result; the next
        identical call is then a new item, not an echo to fold."""
        inner = MagicMock()
        paired = _PairedCodexToolEvents(inner)
        paired.on_tool_call("file_change", {"path": "a.py"})
        paired.on_tool_result(tool="file_change", stdout="", exit_code=None, duration=0.1)
        paired.on_tool_call("file_change", {"path": "a.py"})
        assert inner.on_tool_call.call_count == 2
        inner.on_tool_result.assert_called_once_with(
            tool="file_change", stdout="", exit_code=None, duration=0.1
        )

    def test_changed_args_are_a_new_call_not_an_echo(self):  # noqa: ANN202  # tracked: #288
        inner = MagicMock()
        paired = _PairedCodexToolEvents(inner)
        paired.on_tool_call("file_change", {"path": "a.py"})
        paired.on_tool_call("file_change", {"path": "b.py"})
        assert inner.on_tool_call.call_count == 2
        inner.on_tool_result.assert_not_called()

    def test_other_callbacks_delegate_to_the_inner_handler(self):  # noqa: ANN202  # tracked: #288
        inner = MagicMock()
        paired = _PairedCodexToolEvents(inner)
        paired.on_thinking("hello")
        inner.on_thinking.assert_called_once_with("hello")


class TestProcessStderr:
    def test_stderr_forwarded_to_event_handler(self):  # noqa: ANN202  # tracked: #288
        """Stderr must surface in the log regardless of ``silent``; cli_runner
        always passes ``silent=True`` and the base class's stderr path is
        gated on it."""
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stderr("panic: index out of bounds\n")  # noqa: SLF001  # tracked: #288
        handler.on_thinking.assert_called_once_with("[codex stderr] panic: index out of bounds")
        assert session.stderr_lines == ["panic: index out of bounds\n"]

    def test_stderr_empty_line_ignored(self):  # noqa: ANN202  # tracked: #288
        handler = MagicMock()
        session = _session(event_handler=handler)
        session._process_stderr("\n")  # noqa: SLF001  # tracked: #288
        handler.on_thinking.assert_not_called()


def test_resume_from_adopts_a_checkpoint_when_no_conversation_is_live() -> None:
    agent = _agent()
    agent.session_id = None

    assert agent.resume_from("thread-checkpoint") is True
    assert agent.session_id == "thread-checkpoint"
    assert agent._get_resume_command("prompt", "thread-checkpoint")[:4] == [  # noqa: SLF001
        "/usr/local/bin/codex",
        "exec",
        "resume",
        "thread-checkpoint",
    ]


def test_resume_from_refuses_to_replace_a_live_conversation() -> None:
    agent = _agent()
    agent.session_id = "thread-live"

    assert agent.resume_from("thread-checkpoint") is False
    assert agent.session_id == "thread-live"


def test_forget_session_starts_the_next_turn_fresh() -> None:
    agent = _agent()
    agent.session_id = "thread-live"

    agent.forget_session()
    agent.forget_session()  # idempotent

    assert agent.session_id is None


def test_a_provider_without_a_resume_flag_never_adopts_a_checkpoint() -> None:
    agent = GeminiCodingAgent.__new__(GeminiCodingAgent)
    agent.session_id = None

    assert agent.supports_session_resume is False
    assert agent.resume_from("thread-checkpoint") is False
    assert agent.session_id is None
