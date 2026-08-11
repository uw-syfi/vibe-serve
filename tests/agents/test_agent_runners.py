"""Tests for the :mod:`vibesys.agents` runner abstraction."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vibesys.agent_runner import log_json_and_print, log_prompt_markdown_and_print
from vibesys.agents import build_agent_runner
from vibesys.agents.callbacks import AgentLogger
from vibesys.agents.cli_runner import CliAgentRunner
from vibesys.agents.deepagents_runner import DeepAgentsRunner
from vibesys.agents.progress import RoundProgress
from vibesys.config import Config
from vibesys.constants import ComputeBackend
from vibesys.schemas import (
    IssueJudgeResponse,
    JudgeResponse,
    Verdict,
)
from vs_sandbox import HostResource, ProjectPathPolicy


def _agent_config(**agent) -> Config:  # noqa: ANN003  # tracked: #288
    """Minimal valid Config carrying just an ``[agent]`` section for runner tests."""
    return Config.model_validate({"model": {"name": "m"}, "agent": agent})


def _judge_fallback() -> JudgeResponse:
    return JudgeResponse(
        analysis="fallback",
        feedback="fallback-feedback",
        verdict=Verdict.FAIL,
    )


def test_prompt_markdown_emitter_preserves_raw_log_and_truncates_stdout(capsys, headless_renderer):  # noqa: ANN001, ANN201  # tracked: #288
    headless_renderer.max_text_len = 20
    log = StringIO()
    prompt = "# Title\n\nUse **markdown** and `code`."

    log_prompt_markdown_and_print(prompt, log_file=log)

    stdout = capsys.readouterr().out
    assert "# Title" in stdout
    assert "... [17 more chars, see log for full text]" in stdout
    assert log.getvalue() == prompt + "\n"


def test_json_emitter_preserves_raw_log(capsys):  # noqa: ANN001, ANN201  # tracked: #288
    log = StringIO()
    raw_json = '{"analysis":"ok","items":[1,2]}'

    log_json_and_print(raw_json, log_file=log)

    stdout = capsys.readouterr().out
    assert raw_json in stdout
    assert log.getvalue() == raw_json + "\n"


class TestDeepAgentsRunner:
    """Tests for :class:`DeepAgentsRunner`."""

    def test_deepagents_runner_invoke_returns_structured_response(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        pass_response = JudgeResponse(
            analysis="looks good",
            feedback="",
            verdict=Verdict.PASS,
        )
        with (
            patch("vibesys.agents.deepagents_runner.create_deep_agent") as mock_create,
            patch("vibesys.agents.deepagents_runner.run_typed_agent") as mock_run,
        ):
            mock_create.return_value = MagicMock(name="deep_agent")
            mock_run.return_value = pass_response

            runner = DeepAgentsRunner(
                model="m",
                backends={
                    "implementer": MagicMock(name="impl-backend"),
                    "judge": MagicMock(name="judge-backend"),
                    "perf_eval": MagicMock(name="perf-backend"),
                },
                skills=[],
                model_name="m",
                run_log_file=None,
            )

            result = runner.invoke(
                kind="judge",
                workspace=tmp_path,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=JudgeResponse,
                fallback_factory=_judge_fallback,
                round_label="judge #1",
                progress=RoundProgress(1, 5),
            )

        assert result is pass_response
        assert mock_run.call_count == 1
        _, kwargs = mock_run.call_args
        assert kwargs["response_cls"] is JudgeResponse
        assert kwargs["fallback_factory"] is _judge_fallback
        callbacks = kwargs["callbacks"]
        assert callbacks[0]._progress.label() == "Round 1/5"  # noqa: SLF001  # tracked: #288

    def test_deepagents_runner_picks_backend_by_kind(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        impl_backend = MagicMock(name="impl-backend")
        judge_backend = MagicMock(name="judge-backend")
        perf_backend = MagicMock(name="perf-backend")

        captured_backends: list = []

        def _capture(**kwargs):  # noqa: ANN003, ANN202  # tracked: #288
            captured_backends.append(kwargs["backend"])
            return MagicMock(name="deep_agent")

        with (
            patch(
                "vibesys.agents.deepagents_runner.create_deep_agent",
                side_effect=_capture,
            ),
            patch(
                "vibesys.agents.deepagents_runner.run_typed_agent",
                return_value=_judge_fallback(),
            ),
        ):
            runner = DeepAgentsRunner(
                model="m",
                backends={
                    "implementer": impl_backend,
                    "judge": judge_backend,
                    "perf_eval": perf_backend,
                },
                skills=[],
                model_name="m",
                run_log_file=None,
            )

            for kind in ("implementer", "judge", "perf_eval"):
                runner.invoke(
                    kind=kind,
                    workspace=tmp_path,
                    system_prompt="sys",
                    user_prompt="usr",
                    response_cls=JudgeResponse,
                    fallback_factory=_judge_fallback,
                    round_label=f"{kind} #1",
                )

        assert captured_backends == [impl_backend, judge_backend, perf_backend]

    def test_deepagents_runner_returns_plain_text_without_response_format(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        with (
            patch("vibesys.agents.deepagents_runner.create_deep_agent") as mock_create,
            patch(
                "vibesys.agents.deepagents_runner.run_agent",
                return_value="Natural **Markdown** answer.",
            ) as mock_run,
        ):
            mock_create.return_value = MagicMock(name="chat-agent")
            runner = DeepAgentsRunner(
                model="m",
                backends={"chat": MagicMock(name="chat-backend")},
                skills=[],
                model_name="m",
                run_log_file=None,
            )

            result = runner.invoke_text(
                kind="chat",
                workspace=tmp_path,
                system_prompt="investigate",
                user_prompt="what happened?",
                round_label="experiment chat",
            )

        assert result == "Natural **Markdown** answer."
        assert "response_format" not in mock_create.call_args.kwargs
        assert mock_run.call_args.args[1] == "what happened?"

    def test_deepagents_runner_sessions_are_explicit_and_role_scoped(self):  # noqa: ANN201  # tracked: #288
        runner = DeepAgentsRunner(
            model="m",
            backends={},
            skills=[],
            model_name="m",
            run_log_file=None,
        )

        first = runner._session(kind="implementer", reuse_session=True, session_key="hypothesis:a")  # noqa: SLF001  # tracked: #288
        continued = runner._session(  # noqa: SLF001  # tracked: #288
            kind="implementer", reuse_session=True, session_key="hypothesis:a"
        )
        other_role = runner._session(kind="judge", reuse_session=True, session_key="hypothesis:a")  # noqa: SLF001  # tracked: #288
        fresh = runner._session(kind="implementer", reuse_session=False, session_key="hypothesis:a")  # noqa: SLF001  # tracked: #288

        assert continued is first
        assert other_role is not first
        assert fresh is not first

    def test_deepagents_runner_reuses_graph_with_fresh_default_threads(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """Repeated calls reuse construction but keep default conversations isolated."""
        pass_response = JudgeResponse(
            analysis="looks good",
            feedback="",
            verdict=Verdict.PASS,
        )
        with (
            patch("vibesys.agents.deepagents_runner.create_deep_agent") as mock_create,
            patch(
                "vibesys.agents.deepagents_runner.run_typed_agent",
                return_value=pass_response,
            ) as mock_run,
        ):
            mock_create.return_value = MagicMock(name="deep_agent")
            runner = DeepAgentsRunner(
                model="m",
                backends={"judge": MagicMock(name="judge-backend")},
                skills=[],
                model_name="m",
                run_log_file=None,
            )

            for i in range(2):
                runner.invoke(
                    kind="judge",
                    workspace=tmp_path,
                    system_prompt="sys",
                    user_prompt=f"usr {i}",
                    response_cls=JudgeResponse,
                    fallback_factory=_judge_fallback,
                    round_label=f"judge #{i}",
                )

            assert mock_create.call_count == 1
            thread_ids = [call.kwargs["thread_id"] for call in mock_run.call_args_list]
            assert thread_ids[0] != thread_ids[1]

    def test_deepagents_runner_rebuilds_when_response_schema_changes(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """A different response model must not reuse the old structured graph."""
        with (
            patch("vibesys.agents.deepagents_runner.create_deep_agent") as mock_create,
            patch(
                "vibesys.agents.deepagents_runner.run_typed_agent",
                return_value=JudgeResponse(analysis="ok", feedback="", verdict=Verdict.PASS),
            ),
        ):
            mock_create.return_value = MagicMock(name="deep_agent")
            runner = DeepAgentsRunner(
                model="m",
                backends={"judge": MagicMock(name="judge-backend")},
                skills=[],
                model_name="m",
                run_log_file=None,
            )

            runner.invoke(
                kind="judge",
                workspace=tmp_path,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=JudgeResponse,
                fallback_factory=_judge_fallback,
                round_label="judge #1",
            )
            runner.invoke(
                kind="judge",
                workspace=tmp_path,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=IssueJudgeResponse,
                fallback_factory=lambda: IssueJudgeResponse(
                    issue_id=1,
                    analysis="fallback",
                    feedback="fallback",
                    verdict=Verdict.FAIL,
                ),
                round_label="judge #2",
            )

            assert mock_create.call_count == 2
            assert "response_format" in mock_create.call_args_list[0].kwargs
            assert "response_format" in mock_create.call_args_list[1].kwargs
            assert (
                mock_create.call_args_list[0].kwargs["response_format"].schema
                is not mock_create.call_args_list[1].kwargs["response_format"].schema
            )

    def test_deepagents_runner_rebuilds_when_tool_objects_change(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """Same-named tools can carry different closure-bound behavior."""
        with (
            patch("vibesys.agents.deepagents_runner.create_deep_agent") as mock_create,
            patch(
                "vibesys.agents.deepagents_runner.run_typed_agent",
                return_value=JudgeResponse(analysis="ok", feedback="", verdict=Verdict.PASS),
            ),
        ):
            mock_create.return_value = MagicMock(name="deep_agent")
            runner = DeepAgentsRunner(
                model="m",
                backends={"judge": MagicMock(name="judge-backend")},
                skills=[],
                model_name="m",
                run_log_file=None,
            )

            for tool in (MagicMock(name="same-tool"), MagicMock(name="same-tool")):
                runner.invoke(
                    kind="judge",
                    workspace=tmp_path,
                    system_prompt="sys",
                    user_prompt="usr",
                    response_cls=JudgeResponse,
                    fallback_factory=_judge_fallback,
                    round_label="judge",
                    tools=[tool],
                )

            assert mock_create.call_count == 2

    def test_deepagents_runner_typed_and_text_graphs_are_separate(self, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        with (
            patch("vibesys.agents.deepagents_runner.create_deep_agent") as mock_create,
            patch(
                "vibesys.agents.deepagents_runner.run_typed_agent",
                return_value=JudgeResponse(analysis="ok", feedback="", verdict=Verdict.PASS),
            ),
            patch(
                "vibesys.agents.deepagents_runner.run_agent",
                return_value="plain text",
            ),
        ):
            mock_create.return_value = MagicMock(name="deep_agent")
            runner = DeepAgentsRunner(
                model="m",
                backends={"judge": MagicMock(name="judge-backend")},
                skills=[],
                model_name="m",
                run_log_file=None,
            )

            runner.invoke(
                kind="judge",
                workspace=tmp_path,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=JudgeResponse,
                fallback_factory=_judge_fallback,
                round_label="judge",
            )
            runner.invoke_text(
                kind="judge",
                workspace=tmp_path,
                system_prompt="sys",
                user_prompt="usr",
                round_label="chat",
            )

            assert mock_create.call_count == 2
            assert "response_format" in mock_create.call_args_list[0].kwargs
            assert "response_format" not in mock_create.call_args_list[1].kwargs


# ---------------------------------------------------------------------------
# Helpers for CLI runner tests
# ---------------------------------------------------------------------------


def _make_fake_agent_class(  # noqa: ANN202  # tracked: #288
    *,
    generate_returns: str,
    captured: list,
    generate_raises: type[BaseException] | None = None,
    uninstall_raises: type[Exception] | None = None,
    session_state: dict | None = None,
):
    """Build a fake provider class that records its instances and constructor args.

    When ``session_state`` is provided, ``generate()`` populates
    ``self._last_session`` with a SimpleNamespace carrying ``final_usage``,
    ``total_cost_usd``, and ``duration_ms`` fields — matching the shape
    :class:`CliAgentRunner` reads off ``ClaudeGenerationSession`` after
    ``generate()`` returns.
    """

    from types import SimpleNamespace  # noqa: PLC0415  # tracked: #288

    class FakeAgent:
        supports_native_output_schema = False

        def __init__(self, model=None, event_handler=None):  # noqa: ANN001, ANN204  # tracked: #288
            self.model = model
            self.event_handler = event_handler
            self.env: dict[str, str] = {}
            self.generate_calls: list[dict] = []
            self.install_calls: list[dict] = []
            self.uninstall_calls: list[dict] = []
            self.event_log: list[str] = []
            self._last_session: SimpleNamespace | None = None
            self.reasoning_effort: str | None = None
            self.output_schema_paths: list[str | None] = []
            captured.append(self)

        def set_reasoning_effort(self, effort):  # noqa: ANN001, ANN202  # tracked: #288
            self.reasoning_effort = effort

        def set_output_schema_path(self, path):  # noqa: ANN001, ANN202  # tracked: #288
            self.output_schema_paths.append(path)

        def install_mcp_servers(self, workspace, servers):  # noqa: ANN001, ANN202  # tracked: #288
            self.install_calls.append({"workspace": workspace, "servers": list(servers)})
            self.event_log.append("install")

        def uninstall_mcp_servers(self, workspace, servers):  # noqa: ANN001, ANN202  # tracked: #288
            self.uninstall_calls.append({"workspace": workspace, "servers": list(servers)})
            self.event_log.append("uninstall")
            if uninstall_raises is not None:
                raise uninstall_raises("cleanup boom")  # noqa: TRY003  # tracked: #288

        def generate(self, prompt, cwd=None, timeout=300, silent=False):  # noqa: ANN001, ANN202, FBT002  # tracked: #288
            self.generate_calls.append(
                {
                    "prompt": prompt,
                    "cwd": cwd,
                    "timeout": timeout,
                    "silent": silent,
                }
            )
            self.event_log.append("generate")
            # Mirror :class:`CLICodingAgent.generate`: stash _last_session
            # before invoking the underlying session, so callers can read
            # final state even when the run raises.
            if session_state is not None:
                self._last_session = SimpleNamespace(**session_state)
            if generate_raises is not None:
                raise generate_raises("boom")
            return generate_returns

    return FakeAgent


class TestCliAgentRunner:
    """Tests for :class:`CliAgentRunner`."""

    @pytest.mark.parametrize("provider", ["claude", "gemini", "codex", "opencode"])
    def test_cli_runner_invokes_provider_and_returns_parsed_response(  # noqa: ANN201  # tracked: #288
        self,
        monkeypatch,  # noqa: ANN001  # tracked: #288
        tmp_path,  # noqa: ANN001  # tracked: #288
        provider,  # noqa: ANN001  # tracked: #288
    ):
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            provider,
            fake_cls,
        )

        runner = CliAgentRunner(
            provider=provider,
            model="m",
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        result = runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        assert isinstance(result, JudgeResponse)
        assert result.verdict == Verdict.PASS
        assert len(captured) == 1
        assert captured[0].generate_calls[0]["cwd"] == str(workspace)

    def test_cli_runner_obeys_explicit_session_policy(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "codex",
            fake_cls,
        )
        runner = CliAgentRunner(provider="codex", model="m", run_log_file=None)
        workspace = tmp_path / "ws"
        workspace.mkdir()

        def invoke(*, session_key: str, reuse_session: bool) -> None:
            runner.invoke(
                kind="judge",
                workspace=workspace,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=JudgeResponse,
                fallback_factory=_judge_fallback,
                round_label="review",
                reuse_session=reuse_session,
                session_key=session_key,
            )

        invoke(session_key="hypothesis:a", reuse_session=True)
        invoke(session_key="hypothesis:a", reuse_session=True)
        assert len(captured) == 1
        invoke(session_key="hypothesis:b", reuse_session=True)
        assert len(captured) == 2
        invoke(session_key="hypothesis:a", reuse_session=False)
        assert len(captured) == 3

    def test_codex_persistent_session_renews_before_third_turn(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        original_generate = fake_cls.generate

        def generate(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202  # tracked: #288
            if getattr(self, "session_id", None) is None:
                self.session_id = f"thread-{len(self.generate_calls) + 1}"
            session_ids.append(self.session_id)
            return original_generate(self, *args, **kwargs)

        fake_cls.generate = generate
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "codex",
            fake_cls,
        )
        runner = CliAgentRunner(provider="codex", model="m", run_log_file=None)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        session_ids: list[str] = []

        for _ in range(3):
            runner.invoke(
                kind="judge",
                workspace=workspace,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=JudgeResponse,
                fallback_factory=_judge_fallback,
                round_label="review",
                reuse_session=True,
                session_key="hypothesis:a",
            )

        assert len(captured) == 1
        assert session_ids == ["thread-1", "thread-1", "thread-3"]

    @pytest.mark.parametrize(
        "session_state",
        [
            {
                "final_usage": {"input_tokens": 10_000_000},
                "duration_ms": 1,
            },
            {
                "final_usage": {"input_tokens": 1},
                "duration_ms": 600_000,
            },
        ],
    )
    def test_codex_persistent_session_renews_after_heavy_turn(  # noqa: ANN201  # tracked: #288
        self,
        monkeypatch,  # noqa: ANN001  # tracked: #288
        tmp_path,  # noqa: ANN001  # tracked: #288
        session_state,  # noqa: ANN001  # tracked: #288
    ):
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
            session_state=session_state,
        )
        original_generate = fake_cls.generate

        def generate(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202  # tracked: #288
            if getattr(self, "session_id", None) is None:
                self.session_id = f"thread-{len(self.generate_calls) + 1}"
            session_ids.append(self.session_id)
            return original_generate(self, *args, **kwargs)

        fake_cls.generate = generate
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "codex",
            fake_cls,
        )
        runner = CliAgentRunner(provider="codex", model="m", run_log_file=None)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        session_ids: list[str] = []

        for _ in range(2):
            runner.invoke(
                kind="judge",
                workspace=workspace,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=JudgeResponse,
                fallback_factory=_judge_fallback,
                round_label="review",
                reuse_session=True,
                session_key="hypothesis:a",
            )

        assert session_ids == ["thread-1", "thread-2"]

    def test_cli_runner_selects_models_and_effort_by_loop_role(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "codex",
            fake_cls,
        )
        runner = CliAgentRunner(
            provider="codex",
            model="gpt-5.6-sol",
            model_name="gpt-5.6-sol",
            default_reasoning_effort="high",
            role_models={"implementer": "gpt-5.6-luna"},
            role_reasoning_efforts={
                "orchestrator": "xhigh",
                "implementer": "xhigh",
            },
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        for kind in ("orchestrator", "implementer", "judge"):
            runner.invoke(
                kind=kind,
                workspace=workspace,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=JudgeResponse,
                fallback_factory=_judge_fallback,
                round_label=kind,
            )

        assert [agent.model for agent in captured] == [
            "gpt-5.6-sol",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
        ]
        assert [agent.reasoning_effort for agent in captured] == ["xhigh", "xhigh", "high"]

    @pytest.mark.parametrize("provider", ["claude", "gemini", "codex", "opencode"])
    def test_host_resource_declarations_apply_to_every_local_cli_provider(  # noqa: ANN201  # tracked: #288
        self,
        monkeypatch,  # noqa: ANN001  # tracked: #288
        tmp_path,  # noqa: ANN001  # tracked: #288
        provider,  # noqa: ANN001  # tracked: #288
    ):
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            provider,
            fake_cls,
        )
        builds: list[dict] = []
        cli_runner_module = __import__("vibesys.agents.cli_runner", fromlist=["_"])
        monkeypatch.setattr(
            cli_runner_module,
            "build_host_sandbox",
            lambda *args, **kwargs: builds.append({"args": args, **kwargs}),
        )
        resource = HostResource(
            tmp_path / "toolchain",
            purpose="test toolchain",
        )
        runner = CliAgentRunner(
            provider=provider,
            model="m",
            host_resources=(resource,),
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        result = runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        assert result.verdict == Verdict.PASS
        assert resource in builds[0]["resources"]

    def test_cli_runner_forwards_required_project_path_policy(  # noqa: ANN201  # tracked: #288
        self,
        monkeypatch,  # noqa: ANN001  # tracked: #288
        tmp_path,  # noqa: ANN001  # tracked: #288
    ):
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        cli_runner_module = __import__("vibesys.agents.cli_runner", fromlist=["_"])
        monkeypatch.setitem(cli_runner_module._PROVIDER_CLASSES, "codex", fake_cls)  # noqa: SLF001  # tracked: #288
        sandbox = MagicMock(name="project-sandbox")
        build_sandbox = MagicMock(return_value=sandbox)
        monkeypatch.setattr(cli_runner_module, "build_host_sandbox", build_sandbox)
        policy = ProjectPathPolicy(
            read_only_paths=(".vs",),
            hidden_paths=(".vs/local",),
        )
        runner = CliAgentRunner(
            provider="codex",
            model="m",
            project_path_policy=policy,
            require_host_sandbox=True,
            run_log_file=None,
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        result = runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        assert result.verdict == Verdict.PASS
        assert build_sandbox.call_args.args == (workspace,)
        assert build_sandbox.call_args.kwargs["project_path_policy"] is policy
        assert build_sandbox.call_args.kwargs["require_enforcement"] is True
        assert captured[0].sandbox is sandbox

    def test_cli_runner_falls_back_on_unparseable_output(self, monkeypatch, tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns="# Failure\n\nCould not produce **JSON**.",
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        result = runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        assert isinstance(result, JudgeResponse)
        assert result.verdict == Verdict.FAIL
        assert result.feedback == "fallback-feedback"
        assert result.analysis == "fallback"
        stdout = capsys.readouterr().out
        assert "Failure" in stdout
        assert "# Failure" in stdout
        assert "**JSON**" in stdout

    def test_cli_runner_passes_progress_to_logger(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
            progress=RoundProgress(2, 5),
        )

        assert captured[0].event_handler._progress.label() == "Round 2/5"  # noqa: SLF001  # tracked: #288

    def test_cli_runner_parses_fenced_json(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fenced = '```json\n{"analysis": "fenced", "feedback": "", "verdict": "pass"}\n```'
        fake_cls = _make_fake_agent_class(
            generate_returns=fenced,
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        result = runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        assert result.verdict == Verdict.PASS
        assert result.analysis == "fenced"

    def test_cli_runner_materializes_skills_into_workspace(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        # Tier-organized source tree (like vibesys-skills):
        #   skill_src/
        #     algorithms/myskill/SKILL.md
        #     algorithms/myskill/file.txt
        #     tooling/tool-skill/SKILL.md
        skill_src = tmp_path / "skill_src"
        algo_skill = skill_src / "algorithms" / "myskill"
        algo_skill.mkdir(parents=True)
        (algo_skill / "SKILL.md").write_text("# myskill\n")
        (algo_skill / "file.txt").write_text("hello skill")
        tool_skill = skill_src / "tooling" / "tool-skill"
        tool_skill.mkdir(parents=True)
        (tool_skill / "SKILL.md").write_text("# tool-skill\n")

        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            skills=[skill_src],
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        # Each skill is flattened into every per-CLI discovery path,
        # matching the upstream vibesys-skills install.sh convention.
        for cli_dir in (
            ".claude/skills",
            ".agents/skills",
            ".gemini/skills",
            ".cursor/skills",
            ".opencode/skills",
        ):
            assert (workspace / cli_dir / "myskill" / "SKILL.md").exists()
            assert (workspace / cli_dir / "myskill" / "file.txt").read_text() == "hello skill"
            assert (workspace / cli_dir / "tool-skill" / "SKILL.md").exists()

    def _run_with_platform_skill(self, monkeypatch, tmp_path, compute_backend):  # noqa: ANN001, ANN202  # tracked: #288
        """Materialize a skill carrying references/platforms/<backend>/ trees."""
        skill_src = tmp_path / "serving-systems"
        skill_src.mkdir()
        (skill_src / "SKILL.md").write_text("# serving-systems\n")
        # Portable tier — must survive on every backend.
        algorithms = skill_src / "references" / "algorithms"
        algorithms.mkdir(parents=True)
        (algorithms / "continuous-batching.md").write_text("# contract\n")
        # One directory per backend under references/platforms/.
        for backend in ComputeBackend:
            plat = skill_src / "references" / "platforms" / backend.value
            plat.mkdir(parents=True)
            (plat / "floor.md").write_text(f"# {backend.value} floor\n")
        # A same-named directory *outside* references/platforms/ must not be
        # pruned — prune keys on the parent path, not the bare name.
        decoy = skill_src / "references" / "models" / "cuda"
        decoy.mkdir(parents=True)
        (decoy / "note.md").write_text("# decoy\n")

        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            skills=[skill_src],
            compute_backend=compute_backend,
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )
        return workspace / ".claude/skills" / "serving-systems" / "references"

    @pytest.mark.parametrize(
        "selected", [ComputeBackend.CUDA, ComputeBackend.TRAINIUM, ComputeBackend.METAL]
    )
    def test_materialize_prunes_foreign_platform_dirs(self, monkeypatch, tmp_path, selected):  # noqa: ANN001, ANN201  # tracked: #288
        """Only the selected backend's platform guidance reaches the agent.

        Applying one platform's floor to another produces wrong work — the
        CUDA guidance to eliminate KV padding is inverted on Trainium — so the
        foreign trees must be absent, not merely deprioritized.
        """
        refs = self._run_with_platform_skill(monkeypatch, tmp_path, selected)

        platforms = refs / "platforms"
        assert {p.name for p in platforms.iterdir()} == {selected.value}
        assert (platforms / selected.value / "floor.md").exists()

    def test_materialize_keeps_portable_tiers_and_same_named_dirs(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """Pruning is scoped to references/platforms/, not to directory names."""
        refs = self._run_with_platform_skill(monkeypatch, tmp_path, ComputeBackend.TRAINIUM)

        assert (refs / "algorithms" / "continuous-batching.md").exists()
        # `models/cuda/` shares a name with a backend but is not a platform dir.
        assert (refs / "models" / "cuda" / "note.md").exists()

    def test_materialize_without_backend_keeps_every_platform(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """No selected backend (e.g. non-run tooling) copies the tree intact."""
        refs = self._run_with_platform_skill(monkeypatch, tmp_path, None)

        assert {p.name for p in (refs / "platforms").iterdir()} == {b.value for b in ComputeBackend}

    def test_cli_runner_materializes_single_skill_with_nested_content(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        # Single-skill source (SKILL.md at the root, sub-dirs are reference
        # material inside the one skill). This mirrors the repo's
        # `serving-systems/` layout.
        skill_src = tmp_path / "serving-systems"
        skill_src.mkdir()
        (skill_src / "SKILL.md").write_text("# serving-systems\n")
        sub = skill_src / "algorithms" / "paged-attention"
        sub.mkdir(parents=True)
        (sub / "SKILL.md").write_text("# paged-attention\n")

        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=[],
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            skills=[skill_src],
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        # Root SKILL.md at top level, nested reference SKILL.md preserved.
        for cli_dir in (".claude/skills", ".agents/skills", ".gemini/skills"):
            assert (workspace / cli_dir / "serving-systems" / "SKILL.md").exists()
            assert (
                workspace
                / cli_dir
                / "serving-systems"
                / "algorithms"
                / "paged-attention"
                / "SKILL.md"
            ).exists()

    def test_cli_runner_appends_json_schema_to_prompt(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="THE-SYSTEM-PROMPT",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        assert len(captured) == 1
        prompt = captured[0].generate_calls[0]["prompt"]
        assert "JudgeResponse" in prompt
        assert prompt.startswith("THE-SYSTEM-PROMPT")

    def test_codex_uses_native_schema_without_prompt_duplication(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        fake_cls.supports_native_output_schema = True
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "codex",
            fake_cls,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        runner = CliAgentRunner(provider="codex", model="m", run_log_file=None)

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="THE-SYSTEM-PROMPT",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        agent = captured[0]
        relative = agent.output_schema_paths[-1]
        assert relative is not None
        assert relative.startswith(".cache/vibesys/response-schemas/")
        assert (workspace / relative).is_file()
        assert "Schema for JudgeResponse" not in agent.generate_calls[0]["prompt"]

    def test_codex_schema_materialization_failure_uses_prompt_fallback(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        fake_cls.supports_native_output_schema = True
        runner_module = __import__(
            "vibesys.agents.cli_runner",
            fromlist=["_PROVIDER_CLASSES"],
        )
        monkeypatch.setitem(runner_module._PROVIDER_CLASSES, "codex", fake_cls)  # noqa: SLF001  # tracked: #288
        monkeypatch.setattr(
            runner_module,
            "materialize_native_output_schema",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("unsupported")),
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        log = StringIO()
        runner = CliAgentRunner(provider="codex", model="m", run_log_file=log)

        result = runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="THE-SYSTEM-PROMPT",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        assert result.verdict == Verdict.PASS
        assert captured[0].output_schema_paths == [None]
        assert "Schema for JudgeResponse" in captured[0].generate_calls[0]["prompt"]
        assert "using prompt fallback" in log.getvalue()

    def test_absolute_schema_path_provider_gets_resolved_path(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """Providers reading the schema inline (Claude) get an absolute path,
        resolvable independent of the subprocess cwd, and drop the prompt hint."""
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        fake_cls.supports_native_output_schema = True
        fake_cls.native_output_schema_wants_absolute_path = True
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        runner = CliAgentRunner(provider="claude", model="m", run_log_file=None)

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="THE-SYSTEM-PROMPT",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        agent = captured[0]
        passed = agent.output_schema_paths[-1]
        assert passed is not None
        assert Path(passed).is_absolute()
        assert Path(passed).is_file()
        assert Path(passed).parent == workspace / ".cache/vibesys/response-schemas"
        assert "Schema for JudgeResponse" not in agent.generate_calls[0]["prompt"]

    def test_cli_runner_chat_returns_plain_text_in_a_fresh_session_per_turn(  # noqa: ANN201  # tracked: #288
        self,
        monkeypatch,  # noqa: ANN001  # tracked: #288
        tmp_path,  # noqa: ANN001  # tracked: #288
    ):
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns="Natural **Markdown** answer.",
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "codex",
            fake_cls,
        )

        runner = CliAgentRunner(provider="codex", model="m", run_log_file=None)
        workspace = tmp_path / "ws"
        workspace.mkdir()

        for turn in (1, 2):
            answer = runner.invoke_text(
                kind="chat",
                workspace=workspace,
                system_prompt="sys",
                user_prompt=f"turn {turn}",
                round_label="experiment-chat",
            )
            assert answer == "Natural **Markdown** answer."

        assert len(captured) == 2
        assert captured[0] is not captured[1]
        assert captured[0].generate_calls[0]["prompt"] == "sys\n\nturn 1"
        assert captured[1].generate_calls[0]["prompt"] == "sys\n\nturn 2"
        assert "Return EXACTLY" not in captured[0].generate_calls[0]["prompt"]
        assert "chat" not in runner._agents  # noqa: SLF001  # tracked: #288

    def test_cli_runner_retries_missing_codex_rollout_as_fresh_thread(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []

        class FakeCodexAgent:
            def __init__(self, model=None, event_handler=None):  # noqa: ANN001, ANN204  # tracked: #288
                self.model = model
                self.event_handler = event_handler
                self.env: dict[str, str] = {}
                self.session_id: str | None = "stale-thread"
                self.generate_calls: list[dict] = []
                self._last_session = None
                captured.append(self)

            def install_mcp_servers(self, workspace, servers):  # noqa: ANN001, ANN202  # tracked: #288
                pass

            def uninstall_mcp_servers(self, workspace, servers):  # noqa: ANN001, ANN202  # tracked: #288
                pass

            def generate(self, prompt, cwd=None, timeout=300, silent=False):  # noqa: ANN001, ANN202, ARG002, FBT002  # tracked: #288
                self.generate_calls.append(
                    {"prompt": prompt, "cwd": cwd, "session_id": self.session_id}
                )
                if self.session_id is not None:
                    raise RuntimeError(  # noqa: TRY003  # tracked: #288
                        "thread/resume failed: no rollout found for thread id stale-thread"
                    )
                return '{"analysis": "ok", "feedback": "", "verdict": "pass"}'

        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "codex",
            FakeCodexAgent,
        )

        runner = CliAgentRunner(provider="codex", model="m", run_log_file=None)
        workspace = tmp_path / "ws"
        workspace.mkdir()

        result = runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        assert result.verdict == Verdict.PASS
        assert [call["session_id"] for call in captured[0].generate_calls] == [
            "stale-thread",
            None,
        ]

    def test_cli_runner_prints_prompt_as_rendered_markdown_before_generate(  # noqa: ANN201  # tracked: #288
        self,
        monkeypatch,  # noqa: ANN001  # tracked: #288
        tmp_path,  # noqa: ANN001  # tracked: #288
        capsys,  # noqa: ANN001  # tracked: #288
    ):
        stdout_before_generate: list[str] = []

        class FakeAgent:
            def __init__(self, model=None, event_handler=None):  # noqa: ANN001, ANN204  # tracked: #288
                self.model = model
                self.event_handler = event_handler
                self.env: dict[str, str] = {}

            def install_mcp_servers(self, workspace, servers):  # noqa: ANN001, ANN202  # tracked: #288
                pass

            def uninstall_mcp_servers(self, workspace, servers):  # noqa: ANN001, ANN202  # tracked: #288
                pass

            def generate(self, prompt, cwd=None, timeout=300, silent=False):  # noqa: ANN001, ANN202, ARG002, FBT002  # tracked: #288
                stdout_before_generate.append(capsys.readouterr().out)
                return '{"analysis": "ok", "feedback": "", "verdict": "pass"}'

        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            FakeAgent,
        )

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="# System Prompt\n\nUse **bold** guidance.",
            user_prompt="## User Prompt\n\nRun `pytest`.",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        input_idx = stdout_before_generate[0].index("--- input ---")
        assert "System Prompt" in stdout_before_generate[0][input_idx:]
        assert "User Prompt" in stdout_before_generate[0][input_idx:]
        assert "**bold**" in stdout_before_generate[0][input_idx:]

    def test_cli_runner_writes_usage_jsonl_on_success(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """CliAgentRunner appends one JSON record per invoke() to ``<log_dir>/usage.jsonl``."""
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
            session_state={
                "final_usage": {
                    "input_tokens": 14_000,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 50,
                    "output_tokens": 420,
                },
                "total_cost_usd": 0.0812,
                "duration_ms": 18_431,
            },
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        runner = CliAgentRunner(
            provider="claude",
            model="claude-sonnet-4-6",
            model_name="claude-sonnet-4-6",
            run_log_file=None,
            log_dir=log_dir,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        usage_path = log_dir / "usage.jsonl"
        assert usage_path.exists()
        lines = usage_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["kind"] == "judge"
        assert record["round_label"] == "judge #1"
        assert record["provider"] == "claude"
        assert record["model"] == "claude-sonnet-4-6"
        assert record["input_tokens"] == 14_000
        assert record["cache_creation_input_tokens"] == 200
        assert record["cache_read_input_tokens"] == 50
        assert record["output_tokens"] == 420
        assert record["total_cost_usd"] == 0.0812
        assert record["duration_ms"] == 18_431
        assert "timestamp" in record

    def test_cli_runner_usage_jsonl_appends_across_invocations(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
            session_state={
                "final_usage": {"input_tokens": 1_000, "output_tokens": 10},
                "total_cost_usd": 0.001,
                "duration_ms": 500,
            },
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            run_log_file=None,
            log_dir=log_dir,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        for i in range(3):
            runner.invoke(
                kind="implementer",
                workspace=workspace,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=JudgeResponse,
                fallback_factory=_judge_fallback,
                round_label=f"round #{i}",
            )

        lines = (log_dir / "usage.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3
        labels = [json.loads(line)["round_label"] for line in lines]
        assert labels == ["round #0", "round #1", "round #2"]

    def test_cli_runner_usage_jsonl_written_on_parse_failure(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """Even when the CLI returns unparseable output, the tokens were spent —
        the usage record must still be appended so the audit log is complete."""
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns="not-json-at-all",
            captured=captured,
            session_state={
                "final_usage": {"input_tokens": 7_000, "output_tokens": 100},
                "total_cost_usd": 0.0034,
                "duration_ms": 9_876,
            },
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            run_log_file=None,
            log_dir=log_dir,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        result = runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        # Fallback path fires …
        assert result.verdict == Verdict.FAIL
        # … and usage.jsonl still contains the record.
        usage_path = log_dir / "usage.jsonl"
        assert usage_path.exists()
        record = json.loads(usage_path.read_text().strip().splitlines()[0])
        assert record["input_tokens"] == 7_000
        assert record["total_cost_usd"] == 0.0034

    def test_cli_runner_usage_jsonl_noop_when_log_dir_none(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """Runners built without log_dir (tests, legacy callers) must still succeed."""
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
            session_state={
                "final_usage": {"input_tokens": 5_000},
                "total_cost_usd": 0.002,
                "duration_ms": 1_234,
            },
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            run_log_file=None,
            log_dir=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        result = runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )
        assert result.verdict == Verdict.PASS

    def test_cli_runner_layers_env_into_subprocess_env(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(
            provider="claude",
            model="m",
            run_log_file=None,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            env={"CUDA_VISIBLE_DEVICES": "2"},
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        assert len(captured) == 1
        assert captured[0].env.get("CUDA_VISIBLE_DEVICES") == "2"

    def test_cli_runner_docker_uses_command_executor(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        from types import SimpleNamespace  # noqa: PLC0415  # tracked: #288

        from vibesys.agents.docker_executor import (  # noqa: PLC0415  # tracked: #288
            DockerCommandExecutor,
        )

        captured: list = []
        ownership_repairs: list[tuple[str, int, int]] = []

        monkeypatch.setattr(
            DockerCommandExecutor,
            "repair_workspace_ownership",
            lambda self, *, uid, gid: ownership_repairs.append((self.container_id, uid, gid)),
        )

        class FakeAgent:
            def __init__(self, model=None, event_handler=None, executor=None):  # noqa: ANN001, ANN204  # tracked: #288
                self.model = model
                self.event_handler = event_handler
                self.executor = executor
                self.env: dict[str, str] = {}
                self.generate_calls: list[dict] = []
                self._last_session = SimpleNamespace()
                captured.append(self)

            def install_mcp_servers(self, workspace, servers):  # noqa: ANN001, ANN202, ARG002  # tracked: #288
                return None

            def uninstall_mcp_servers(self, workspace, servers):  # noqa: ANN001, ANN202, ARG002  # tracked: #288
                return None

            def generate(self, prompt, cwd=None, timeout=300, silent=False):  # noqa: ANN001, ANN202, FBT002  # tracked: #288
                self.generate_calls.append(
                    {
                        "prompt": prompt,
                        "cwd": cwd,
                        "timeout": timeout,
                        "silent": silent,
                    }
                )
                return '{"analysis": "ok", "feedback": "", "verdict": "pass"}'

        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            FakeAgent,
        )

        sandbox = SimpleNamespace(_container_id="container-one")
        runner = CliAgentRunner(
            provider="claude",
            model="m",
            run_log_file=None,
            docker_sandboxes={"judge": sandbox},
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
        )

        assert isinstance(captured[0].executor, DockerCommandExecutor)
        assert captured[0].executor.container_id == "container-one"
        assert captured[0].generate_calls[0]["cwd"] is None

        sandbox._container_id = "container-two"  # noqa: SLF001  # tracked: #288
        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #2",
        )

        assert len(captured) == 1
        assert captured[0].executor.container_id == "container-two"
        assert [container_id for container_id, _, _ in ownership_repairs] == [
            "container-one",
            "container-two",
        ]

    def test_cli_runner_invokes_install_then_generate_then_uninstall(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """The mcp_servers kwarg triggers a strict install → generate → uninstall sandwich."""
        from vibesys._agent_cli.base import MCPServerSpec  # noqa: PLC0415  # tracked: #288

        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(provider="claude", model="m", run_log_file=None)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        spec = MCPServerSpec(name="vibesys-issues", command="python", args=["-m", "x"])

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
            mcp_servers=[spec],
        )

        assert len(captured) == 1
        agent = captured[0]
        # Strict ordering: install before generate before uninstall.
        assert agent.event_log == ["install", "generate", "uninstall"]
        assert agent.install_calls[0]["workspace"] == workspace
        assert agent.install_calls[0]["servers"] == [spec]
        assert agent.uninstall_calls[0]["workspace"] == workspace
        assert agent.uninstall_calls[0]["servers"] == [spec]

    def test_cli_runner_uninstalls_even_when_generate_raises(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """uninstall_mcp_servers must run in finally so a crashing generate
        doesn't leave stale config in the workspace."""
        from vibesys._agent_cli.base import MCPServerSpec  # noqa: PLC0415  # tracked: #288

        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns="",
            captured=captured,
            generate_raises=RuntimeError,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(provider="claude", model="m", run_log_file=None)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        spec = MCPServerSpec(name="vibesys-issues", command="python", args=["-m", "x"])

        with pytest.raises(RuntimeError, match="boom"):
            runner.invoke(
                kind="judge",
                workspace=workspace,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=JudgeResponse,
                fallback_factory=_judge_fallback,
                round_label="judge #1",
                mcp_servers=[spec],
            )

        agent = captured[0]
        assert agent.event_log == ["install", "generate", "uninstall"]

    def test_cli_runner_preserves_generate_error_when_uninstall_also_raises(  # noqa: ANN201  # tracked: #288
        self,
        monkeypatch,  # noqa: ANN001  # tracked: #288
        tmp_path,  # noqa: ANN001  # tracked: #288
    ):
        from vibesys._agent_cli.base import MCPServerSpec  # noqa: PLC0415  # tracked: #288

        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns="",
            captured=captured,
            generate_raises=RuntimeError,
            uninstall_raises=OSError,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )
        runner = CliAgentRunner(provider="claude", model="m", run_log_file=None)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        spec = MCPServerSpec(name="vibesys-issues", command="python", args=["-m", "x"])

        with pytest.raises(RuntimeError, match="boom"):
            runner.invoke(
                kind="judge",
                workspace=workspace,
                system_prompt="sys",
                user_prompt="usr",
                response_cls=JudgeResponse,
                fallback_factory=_judge_fallback,
                round_label="judge #1",
                mcp_servers=[spec],
            )

        assert captured[0].event_log == ["install", "generate", "uninstall"]

    def test_cli_runner_skips_install_uninstall_when_no_mcp_servers(self, monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
        """When mcp_servers is None or omitted, install/uninstall hooks are
        not called at all."""
        captured: list = []
        fake_cls = _make_fake_agent_class(
            generate_returns='{"analysis": "ok", "feedback": "", "verdict": "pass"}',
            captured=captured,
        )
        monkeypatch.setitem(
            __import__(  # noqa: SLF001  # tracked: #288
                "vibesys.agents.cli_runner",
                fromlist=["_PROVIDER_CLASSES"],
            )._PROVIDER_CLASSES,
            "claude",
            fake_cls,
        )

        runner = CliAgentRunner(provider="claude", model="m", run_log_file=None)
        workspace = tmp_path / "ws"
        workspace.mkdir()

        runner.invoke(
            kind="judge",
            workspace=workspace,
            system_prompt="sys",
            user_prompt="usr",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="judge #1",
            # mcp_servers omitted
        )

        agent = captured[0]
        assert agent.install_calls == []
        assert agent.uninstall_calls == []
        assert agent.event_log == ["generate"]


class TestBuildAgentRunner:
    """Tests for :func:`build_agent_runner`."""

    def test_build_agent_runner_default_is_cli(self):  # noqa: ANN201  # tracked: #288
        runner = build_agent_runner(
            _agent_config(),
            agent_backend=None,
            cli_provider=None,
            backends={
                "implementer": MagicMock(),
                "judge": MagicMock(),
                "perf_eval": MagicMock(),
            },
            skills=[],
            skill_source_dirs=[],
            model="m",
            model_name="m",
            run_log_file=None,
            use_docker=False,
        )
        assert runner.backend_name == "cli"
        assert runner._provider == "codex"  # noqa: SLF001  # tracked: #288

    def test_build_agent_runner_cli_provider_from_config(self):  # noqa: ANN201  # tracked: #288
        runner = build_agent_runner(
            _agent_config(backend="cli", cli_provider="claude"),
            agent_backend=None,
            cli_provider=None,
            backends=None,
            skills=[],
            skill_source_dirs=[],
            model=None,
            model_name="m",
            run_log_file=None,
            use_docker=False,
        )
        assert runner.backend_name == "cli"
        assert runner._provider == "claude"  # noqa: SLF001  # tracked: #288

    def test_build_agent_runner_cli_defaults_to_codex(self):  # noqa: ANN201  # tracked: #288
        """When backend=cli and no provider specified, defaults to codex."""
        runner = build_agent_runner(
            _agent_config(backend="cli"),
            agent_backend=None,
            cli_provider=None,
            backends=None,
            skills=[],
            skill_source_dirs=[],
            model=None,
            model_name="m",
            run_log_file=None,
            use_docker=False,
        )
        assert runner.backend_name == "cli"
        assert runner._provider == "codex"  # noqa: SLF001  # tracked: #288

    def test_build_agent_runner_cli_docker_returns_cli_runner(self):  # noqa: ANN201  # tracked: #288
        """cli backend + docker now returns a CliAgentRunner with docker_sandboxes."""
        from unittest.mock import MagicMock  # noqa: PLC0415  # tracked: #288

        mock_backends = {
            "implementer": MagicMock(),
            "judge": MagicMock(),
            "perf_eval": MagicMock(),
        }
        runner = build_agent_runner(
            _agent_config(),
            agent_backend="cli",
            cli_provider="claude",
            backends=mock_backends,
            skills=[],
            skill_source_dirs=[],
            model=None,
            model_name="m",
            run_log_file=None,
            use_docker=True,
        )
        assert isinstance(runner, CliAgentRunner)
        assert runner._docker_sandboxes is mock_backends  # noqa: SLF001  # tracked: #288

    def test_build_agent_runner_rejects_unsupported_docker_provider(self):  # noqa: ANN201  # tracked: #288
        with pytest.raises(SystemExit, match="not yet supported with --docker"):
            build_agent_runner(
                _agent_config(),
                agent_backend="cli",
                cli_provider="nonexistent",
                backends={},
                skills=[],
                skill_source_dirs=[],
                model=None,
                model_name="m",
                run_log_file=None,
                use_docker=True,
            )

    def test_build_agent_runner_rejects_unknown_backend(self):  # noqa: ANN201  # tracked: #288
        with pytest.raises(SystemExit, match="unknown agent backend"):
            build_agent_runner(
                _agent_config(),
                agent_backend="bogus",
                cli_provider=None,
                backends=None,
                skills=[],
                skill_source_dirs=[],
                model=None,
                model_name="m",
                run_log_file=None,
                use_docker=False,
            )

    def test_required_project_enforcement_rejects_deepagents(self):  # noqa: ANN201  # tracked: #288
        with pytest.raises(SystemExit, match="requires the CLI agent backend"):
            build_agent_runner(
                _agent_config(backend="deepagents"),
                agent_backend=None,
                cli_provider=None,
                backends={"implementer": MagicMock()},
                skills=[],
                skill_source_dirs=[],
                model="m",
                model_name="m",
                run_log_file=None,
                use_docker=False,
                require_host_sandbox=True,
            )

    def test_required_project_enforcement_rejects_omnigent(self):  # noqa: ANN201  # tracked: #288
        config = Config.model_validate(
            {
                "model": {"name": "m"},
                "agent": {"backend": "cli", "cli_provider": "codex"},
                "feature_flags": {"omnigent_agent_backend": True},
            }
        )

        with pytest.raises(SystemExit, match="does not yet support the Omnigent backend"):
            build_agent_runner(
                config,
                agent_backend=None,
                cli_provider=None,
                backends=None,
                skills=[],
                skill_source_dirs=[],
                model=None,
                model_name="m",
                run_log_file=None,
                use_docker=False,
                require_host_sandbox=True,
            )

    def test_required_project_enforcement_permits_stub(self):  # noqa: ANN201  # tracked: #288
        runner = build_agent_runner(
            _agent_config(backend="stub"),
            agent_backend=None,
            cli_provider=None,
            backends=None,
            skills=[],
            skill_source_dirs=[],
            model=None,
            model_name="m",
            run_log_file=None,
            use_docker=False,
            require_host_sandbox=True,
        )

        assert runner.backend_name == "stub"

    def test_build_agent_runner_forwards_project_policy_to_cli(self):  # noqa: ANN201  # tracked: #288
        policy = ProjectPathPolicy(
            read_only_paths=(".vs",),
            hidden_paths=(".vs/local",),
        )

        runner = build_agent_runner(
            _agent_config(backend="cli", cli_provider="codex"),
            agent_backend=None,
            cli_provider=None,
            backends=None,
            skills=[],
            skill_source_dirs=[],
            model=None,
            model_name="m",
            run_log_file=None,
            use_docker=False,
            project_path_policy=policy,
            require_host_sandbox=True,
        )

        assert isinstance(runner, CliAgentRunner)
        assert runner._project_path_policy is policy  # noqa: SLF001  # tracked: #288
        assert runner._require_host_sandbox is True  # noqa: SLF001  # tracked: #288

    # --- model resolution for the cli backend ---------------------------------
    #
    # Regression coverage for the config API where [model].name did not reach
    # the CLI tool. [model].name is the single source of truth: it must be the
    # model handed to the CLI tool, and the displayed model_name must equal it
    # so the run-log header can't report a model that isn't running.

    @staticmethod
    def _cli_runner(config, *, model_name):  # noqa: ANN001, ANN205  # tracked: #288
        return build_agent_runner(
            config,
            agent_backend=None,
            cli_provider=None,
            backends=None,
            skills=[],
            skill_source_dirs=[],
            model=None,
            model_name=model_name,
            run_log_file=None,
            use_docker=False,
        )

    @pytest.mark.parametrize("provider", ["claude", "gemini", "codex", "opencode"])
    def test_cli_backend_uses_model_name(self, provider):  # noqa: ANN001, ANN201  # tracked: #288
        runner = self._cli_runner(
            _agent_config(backend="cli", cli_provider=provider),
            model_name="gpt-5.4",
        )
        assert runner._model == "gpt-5.4"  # noqa: SLF001  # tracked: #288

    def test_displayed_model_name_matches_model_passed(self):  # noqa: ANN201  # tracked: #288
        # The run-log header prints _model_name; it must equal the model
        # actually handed to the CLI tool so the log never reports a model
        # that isn't running.
        runner = self._cli_runner(
            _agent_config(backend="cli", cli_provider="codex"),
            model_name="gpt-5.4",
        )
        assert runner._model_name == runner._model == "gpt-5.4"  # noqa: SLF001  # tracked: #288

    def test_cli_backend_carries_outer_and_inner_role_configuration(self):  # noqa: ANN201  # tracked: #288
        config = _agent_config(
            backend="cli",
            cli_provider="codex",
            outer={"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
            inner={"model": "gpt-5.6-luna", "reasoning_effort": "xhigh"},
        )
        config.thinking.level = "high"
        runner = self._cli_runner(config, model_name="gpt-5.6-sol")

        assert runner._default_reasoning_effort == "high"  # noqa: SLF001  # tracked: #288
        assert runner._role_models == {  # noqa: SLF001  # tracked: #288
            "orchestrator": "gpt-5.6-sol",
            "implementer": "gpt-5.6-luna",
        }
        assert runner._role_reasoning_efforts == {  # noqa: SLF001  # tracked: #288
            "orchestrator": "xhigh",
            "implementer": "xhigh",
        }


class TestAgentLoggerEventHandler:
    """Tests for :class:`AgentLogger` as a CLI event handler."""

    def test_agent_logger_event_handler_methods_drive_formatters(self):  # noqa: ANN201  # tracked: #288
        log_file = MagicMock()
        logger = AgentLogger(
            log_file=log_file,
            model_name="m",
            agent_label="Judge",
        )

        with (
            patch.object(logger, "log_tool_call") as mock_tool_call,
            patch.object(logger, "log_tool_result") as mock_tool_result,
        ):
            logger.on_thinking("hello")
            logger.on_tool_call("Bash", {"command": "ls"})
            logger.on_tool_result("Bash", stdout="output", exit_code=0)
            logger.on_tool_result("Bash", stderr="boom", exit_code=1)

        mock_tool_call.assert_called_once_with("Bash", {"command": "ls"})
        assert mock_tool_result.call_count == 2

        ok_call = mock_tool_result.call_args_list[0]
        assert ok_call.args[0] == "Bash"
        assert ok_call.args[1] == "output"
        assert ok_call.kwargs.get("is_error") is False

        err_call = mock_tool_result.call_args_list[1]
        assert err_call.args[0] == "Bash"
        assert err_call.args[1] == "boom"
        assert err_call.kwargs.get("is_error") is True

    def test_agent_logger_event_handler_forwards_usage(self):  # noqa: ANN201  # tracked: #288
        log_file = MagicMock()
        logger = AgentLogger(
            log_file=log_file,
            model_name="claude-sonnet-4-6",
            agent_label="Implementer",
        )

        usage = {
            "input_tokens": 12_345,
            "output_tokens": 67,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        logger.on_usage(usage)

        assert logger._input_tokens == 12_345  # noqa: SLF001  # tracked: #288
        assert logger._latest_usage == usage  # noqa: SLF001  # tracked: #288


class TestBuildAgentRunnerBackendSelection:
    """``build_agent_runner`` backend resolution.

    The default agent backend is ``"cli"`` (provider ``"codex"``) when neither
    the ``--agent-backend`` flag nor an ``[agent].backend`` config key is set.
    Pinned here so the default cannot silently flip.
    """

    def _build(self, config, *, agent_backend=None, cli_provider=None):  # noqa: ANN001, ANN202  # tracked: #288
        return build_agent_runner(
            config,
            agent_backend=agent_backend,
            cli_provider=cli_provider,
            backends=None,
            skills=[],
            skill_source_dirs=[],
            model=None,
            model_name="",
            run_log_file=None,
            use_docker=False,
        )

    def test_default_backend_is_cli_with_empty_config(self):  # noqa: ANN201  # tracked: #288
        runner = self._build(_agent_config())
        assert isinstance(runner, CliAgentRunner)
        assert runner._provider == "codex"  # noqa: SLF001  # tracked: #288

    def test_empty_agent_section_defaults_to_cli(self):  # noqa: ANN201  # tracked: #288
        runner = self._build(_agent_config())
        assert isinstance(runner, CliAgentRunner)
        assert runner._provider == "codex"  # noqa: SLF001  # tracked: #288

    def test_agent_backend_flag_overrides_config(self):  # noqa: ANN201  # tracked: #288
        # An explicit --agent-backend flag wins over [agent].backend.
        runner = self._build(_agent_config(backend="deepagents"), agent_backend="cli")
        assert isinstance(runner, CliAgentRunner)

    def test_config_can_select_cli_provider(self):  # noqa: ANN201  # tracked: #288
        runner = self._build(_agent_config(backend="cli", cli_provider="claude"))
        assert isinstance(runner, CliAgentRunner)
        assert runner._provider == "claude"  # noqa: SLF001  # tracked: #288
