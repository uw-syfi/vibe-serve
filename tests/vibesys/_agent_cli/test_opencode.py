"""Unit tests for the Opencode provider's command construction and error capture.

These tests exercise :mod:`vibesys._agent_cli.opencode` without spawning the
real ``opencode`` binary:

1. ``_get_command`` carries no prompt positional (the prompt goes to stdin, as
   for every provider; a positional would send it twice and hit the argv limit
   on long orchestrator prompts).
2. ``_get_resume_command`` continues the same session with ``--session``, and
   ``_extract_session_id`` reads the id the JSON stream reported.
3. ``{"type":"error"}`` stdout events are kept and appended to the failure
   message when a run exits non-zero.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vibesys._agent_cli.opencode import OpencodeCodingAgent, _ErrorCapturingOpencodeSession


def _agent(model: str | None = "openrouter/some-model") -> OpencodeCodingAgent:
    """Build an OpencodeCodingAgent without running binary detection."""
    agent = OpencodeCodingAgent.__new__(OpencodeCodingAgent)
    agent.binary_path = "/usr/local/bin/opencode"
    agent.model = model
    return agent


def _session() -> _ErrorCapturingOpencodeSession:
    return _ErrorCapturingOpencodeSession(
        binary_name="opencode",
        env={},
        log_prefix="[Opencode]",
        cmd=["opencode", "run", "--format=json"],
        logger=MagicMock(),
        silent=True,
        event_handler=None,
    )


class TestGetCommand:
    def test_initial_command_has_no_prompt_positional(self) -> None:  # tracked: #288
        cmd = _agent()._get_command("a very long prompt")  # noqa: SLF001  # tracked: #288
        assert cmd == [
            "/usr/local/bin/opencode",
            "run",
            "--model",
            "openrouter/some-model",
            "--format=json",
        ]
        assert "a very long prompt" not in " ".join(cmd)

    def test_initial_command_omits_model_when_unset(self) -> None:  # tracked: #288
        cmd = _agent(model=None)._get_command("prompt")  # noqa: SLF001  # tracked: #288
        assert cmd == ["/usr/local/bin/opencode", "run", "--format=json"]

    def test_resume_command_continues_the_session(self) -> None:  # tracked: #288
        cmd = _agent()._get_resume_command("prompt", "ses_123")  # noqa: SLF001  # tracked: #288
        assert cmd == [
            "/usr/local/bin/opencode",
            "run",
            "--session",
            "ses_123",
            "--model",
            "openrouter/some-model",
            "--format=json",
        ]

    def test_extract_session_id_reads_the_stream_id(self) -> None:  # tracked: #288
        session = _session()
        assert _agent()._extract_session_id(session) is None  # noqa: SLF001  # tracked: #288
        session._process_stdout('{"type":"text","sessionID":"ses_abc","part":{"text":"hi"}}\n')  # noqa: SLF001  # tracked: #288
        assert _agent()._extract_session_id(session) == "ses_abc"  # noqa: SLF001  # tracked: #288


class TestErrorCapture:
    def test_error_events_are_appended_to_the_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _session()
        session._process_stdout(  # noqa: SLF001  # tracked: #288
            '{"type":"error","error":{"name":"ProviderError","data":{"message":"rate limited"}}}\n'
        )  # tracked: #288

        def failing_run(self: object, prompt: str) -> str:  # noqa: ARG001
            raise RuntimeError("opencode exited with code 1: ")  # noqa: TRY003  # tracked: #288

        monkeypatch.setattr(
            "agentshim.cli_agent.CLIGenerationSession.run", failing_run, raising=True
        )
        with pytest.raises(RuntimeError) as info:
            session.run("prompt")
        message = str(info.value)
        assert message.startswith("opencode exited with code 1")
        assert "opencode error events:" in message
        assert "rate limited" in message

    def test_failure_without_error_events_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _session()

        def failing_run(self: object, prompt: str) -> str:  # noqa: ARG001
            raise RuntimeError("opencode exited with code 1: boom")  # noqa: TRY003  # tracked: #288

        monkeypatch.setattr(
            "agentshim.cli_agent.CLIGenerationSession.run", failing_run, raising=True
        )
        with pytest.raises(RuntimeError, match=r"^opencode exited with code 1: boom$"):
            session.run("prompt")
