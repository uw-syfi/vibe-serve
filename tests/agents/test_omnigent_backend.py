"""Tests for the opt-in Omnigent agent backend.

The contract these tests protect is asymmetric on purpose:

- With ``omnigent_agent_backend`` off (the default), ``build_agent_runner``
  must behave exactly as it did before the flag existed — same runner class,
  same provider resolution, same Docker handling. Those cases are the
  regression guard for the agentshim path.
- With the flag on, every unsupported combination must fail loudly and name
  the remedy, because a silent fallback to agentshim would make run logs
  misattribute which stack produced a result.

Tests that need the real ``omnigent`` package are skipped when it is absent
(it requires Python 3.12+ and is an optional extra), so the suite stays green
on the 3.11 baseline CI runs.
"""

from __future__ import annotations

import importlib.util
import json
import os
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibesys.agents import build_agent_runner
from vibesys.agents.cli_runner import CliAgentRunner
from vibesys.agents.omnigent import supported_providers
from vibesys.agents.omnigent.providers import OMNIGENT_PROVIDER_EXECUTORS
from vibesys.agents.omnigent.runner import (
    OmnigentAgentRunner,
    OmnigentUnavailableError,
    _patched_environ,
    resolve_executor_spec,
)
from vibesys.config import Config
from vibesys.features import FeatureFlag, is_feature_enabled
from vibesys.schemas import JudgeResponse, Verdict
from vs_sandbox import HostResource, HostResourceAccess


def _config(*, omnigent: bool | None = None, **agent) -> Config:
    """Minimal Config, optionally overriding the omnigent feature flag."""
    payload: dict = {"model": {"name": "m"}, "agent": agent}
    if omnigent is not None:
        payload["feature_flags"] = {"omnigent_agent_backend": omnigent}
    return Config.model_validate(payload)


def _build(config: Config, **overrides):
    kwargs = {
        "agent_backend": None,
        "cli_provider": None,
        "backends": None,
        "skills": [],
        "skill_source_dirs": [],
        "model": None,
        "model_name": "m",
        "run_log_file": None,
        "use_docker": False,
    }
    kwargs.update(overrides)
    return build_agent_runner(config, **kwargs)


def _judge_fallback() -> JudgeResponse:
    return JudgeResponse(analysis="fb", feedback="fb", verdict=Verdict.FAIL)


class TestFlagDefaultsOff:
    """The agentshim path must be untouched unless the flag is set."""

    def test_flag_defaults_to_disabled(self):
        assert is_feature_enabled(FeatureFlag.OMNIGENT_AGENT_BACKEND, _config()) is False

    def test_flag_absent_from_config_yields_cli_runner(self):
        runner = _build(_config(backend="cli", cli_provider="claude"))

        assert isinstance(runner, CliAgentRunner)
        assert runner.backend_name == "cli"
        assert runner._provider == "claude"

    def test_flag_explicitly_false_yields_cli_runner(self):
        runner = _build(_config(omnigent=False, backend="cli", cli_provider="codex"))

        assert isinstance(runner, CliAgentRunner)
        assert runner.backend_name == "cli"

    @pytest.mark.parametrize("provider", ["claude", "gemini", "codex", "opencode"])
    def test_every_agentshim_provider_still_builds_with_flag_off(self, provider):
        """Providers omnigent cannot run must keep working on the default path."""
        runner = _build(_config(backend="cli", cli_provider=provider))

        assert isinstance(runner, CliAgentRunner)
        assert runner._provider == provider

    def test_docker_path_unchanged_with_flag_off(self):
        backends = {"implementer": MagicMock(), "judge": MagicMock(), "perf_eval": MagicMock()}

        runner = _build(
            _config(backend="cli", cli_provider="claude"),
            backends=backends,
            use_docker=True,
        )

        assert isinstance(runner, CliAgentRunner)
        assert runner._docker_sandboxes is backends


class TestFlagOnSelection:
    def test_flag_on_yields_omnigent_runner(self):
        runner = _build(_config(omnigent=True, backend="cli", cli_provider="claude"))

        assert isinstance(runner, OmnigentAgentRunner)
        assert runner.backend_name == "omnigent"
        assert runner._provider == "claude"

    def test_flag_on_passes_through_model_and_log_dir(self, tmp_path):
        runner = _build(
            _config(omnigent=True, backend="cli", cli_provider="codex"),
            model_name="gpt-5",
            log_dir=tmp_path,
        )

        assert runner._model == "gpt-5"
        assert runner._model_name == "gpt-5"
        assert runner._log_dir == tmp_path

    def test_flag_on_does_not_affect_deepagents_backend(self):
        """The flag scopes to the cli backend only."""
        runner = _build(
            _config(omnigent=True, backend="deepagents"),
            backends={"implementer": MagicMock()},
        )

        assert runner.backend_name == "deepagents"

    def test_flag_on_does_not_affect_stub_backend(self):
        runner = _build(_config(omnigent=True, backend="stub"))

        assert runner.backend_name == "stub"

    @pytest.mark.parametrize("provider", ["gemini", "opencode"])
    def test_unsupported_provider_names_the_remedy(self, provider):
        with pytest.raises(OmnigentUnavailableError) as exc:
            _build(_config(omnigent=True, backend="cli", cli_provider=provider))

        message = str(exc.value)
        assert provider in message
        assert "omnigent_agent_backend" in message
        assert "claude" in message and "codex" in message
        assert "agentshim" in message

    def test_docker_combination_is_rejected(self):
        backends = {"implementer": MagicMock(), "judge": MagicMock(), "perf_eval": MagicMock()}

        with pytest.raises(OmnigentUnavailableError) as exc:
            _build(
                _config(omnigent=True, backend="cli", cli_provider="claude"),
                backends=backends,
                use_docker=True,
            )

        assert "--docker" in str(exc.value)

    def test_host_resource_grants_are_refused_rather_than_dropped(self, tmp_path):
        """Silently dropping an operator's grant would weaken a security boundary."""
        grant = HostResource(tmp_path / "models", HostResourceAccess.READ_ONLY, "weights")

        with pytest.raises(OmnigentUnavailableError) as exc:
            _build(
                _config(omnigent=True, backend="cli", cli_provider="claude"),
                host_resources=[grant],
            )

        message = str(exc.value)
        assert "models" in message
        assert "agentshim" in message

    def test_empty_host_resources_are_fine(self):
        runner = _build(
            _config(omnigent=True, backend="cli", cli_provider="claude"),
            host_resources=(),
        )

        assert isinstance(runner, OmnigentAgentRunner)


class TestProviderRegistry:
    def test_supported_providers_are_claude_and_codex(self):
        assert supported_providers() == ["claude", "codex"]

    @pytest.mark.parametrize("provider", ["claude", "codex"])
    def test_specs_point_at_omnigent_inner_executors(self, provider):
        spec = OMNIGENT_PROVIDER_EXECUTORS[provider]

        assert spec.module.startswith("omnigent.inner.")
        assert spec.class_name.endswith("Executor")
        assert spec.harness

    def test_resolve_rejects_unknown_provider(self):
        with pytest.raises(OmnigentUnavailableError):
            resolve_executor_spec("does-not-exist")


class TestMissingDependency:
    def test_import_error_names_the_extra(self, monkeypatch):
        runner = OmnigentAgentRunner(provider="claude")
        monkeypatch.setattr(
            "vibesys.agents.omnigent.runner.import_module",
            MagicMock(side_effect=ImportError("No module named 'omnigent'")),
        )

        with pytest.raises(OmnigentUnavailableError) as exc:
            runner._executor_class()

        message = str(exc.value)
        assert "--extra omnigent" in message
        assert "3.12" in message

    def test_incompatible_version_names_the_class(self, monkeypatch):
        runner = OmnigentAgentRunner(provider="codex")
        monkeypatch.setattr(
            "vibesys.agents.omnigent.runner.import_module",
            MagicMock(return_value=object()),
        )

        with pytest.raises(OmnigentUnavailableError) as exc:
            runner._executor_class()

        assert "CodexExecutor" in str(exc.value)


class TestPatchedEnviron:
    def test_sets_and_restores_a_new_key(self):
        key = "VIBESYS_OMNIGENT_TEST_UNSET"
        os.environ.pop(key, None)

        with _patched_environ({key: "1"}):
            assert os.environ[key] == "1"

        assert key not in os.environ

    def test_restores_a_preexisting_value(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")

        with _patched_environ({"CUDA_VISIBLE_DEVICES": "3"}):
            assert os.environ["CUDA_VISIBLE_DEVICES"] == "3"

        assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"

    def test_restores_on_exception(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")

        with pytest.raises(RuntimeError), _patched_environ({"CUDA_VISIBLE_DEVICES": "3"}):
            raise RuntimeError("boom")

        assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"

    def test_none_is_a_noop(self):
        before = dict(os.environ)

        with _patched_environ(None):
            pass

        assert dict(os.environ) == before


class TestMcpRejection:
    def test_mcp_servers_are_refused_rather_than_dropped(self, tmp_path):
        runner = OmnigentAgentRunner(provider="claude")
        server = MagicMock()
        server.name = "issue-board"

        with pytest.raises(OmnigentUnavailableError) as exc:
            runner.invoke_text(
                kind="implementer",
                workspace=tmp_path,
                system_prompt="sys",
                user_prompt="do it",
                round_label="r1",
                mcp_servers=[server],
            )

        assert "issue-board" in str(exc.value)


class TestUsageRecord:
    def test_record_matches_the_agentshim_schema(self, tmp_path):
        runner = OmnigentAgentRunner(provider="claude", model_name="m", log_dir=tmp_path)

        runner._write_usage_record(
            kind="judge",
            round_label="judge #1",
            usage={"input_tokens": 11, "output_tokens": 22},
        )

        records = [
            json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines() if line
        ]
        assert len(records) == 1
        record = records[0]
        assert record["kind"] == "judge"
        assert record["provider"] == "claude"
        assert record["input_tokens"] == 11
        assert record["output_tokens"] == 22
        # Omnigent reports no cost/duration; absence must not be faked as 0.
        assert record["total_cost_usd"] is None
        assert record["duration_ms"] is None

    def test_no_log_dir_writes_nothing(self, tmp_path):
        runner = OmnigentAgentRunner(provider="claude")

        runner._write_usage_record(kind="judge", round_label="r", usage={})

        assert list(tmp_path.iterdir()) == []


requires_omnigent = pytest.mark.skipif(
    importlib.util.find_spec("omnigent") is None,
    reason="omnigent is an optional extra requiring Python 3.12+",
)


class _FakeExecutor:
    """Stands in for an Omnigent executor, emitting a scripted event stream."""

    def __init__(self, events):
        self._events = events
        self.calls: list[tuple] = []

    def run_turn(self, messages, tools, system_prompt, config=None):
        self.calls.append((messages, tools, system_prompt, config))

        async def _stream():
            for event in self._events:
                yield event

        return _stream()


@requires_omnigent
class TestDriveTurn:
    """Exercises the real Omnigent event types against the adapter."""

    def _run(self, events, log=None):
        import asyncio

        from vibesys.agents.callbacks import AgentLogger
        from vibesys.agents.omnigent.runner import _drive_turn

        executor = _FakeExecutor(events)
        logger = AgentLogger(log_file=log, agent_label="Judge")
        text, usage = asyncio.run(
            _drive_turn(executor, prompt="p", system_prompt="s", logger=logger)
        )
        return executor, text, usage

    def test_turn_complete_response_wins_over_chunks(self):
        from omnigent import TextChunk, TurnComplete

        _, text, _ = self._run([TextChunk(text="partial"), TurnComplete(response="final answer")])

        assert text == "final answer"

    def test_chunks_are_the_fallback_when_response_is_absent(self):
        from omnigent import TextChunk, TurnComplete

        _, text, _ = self._run(
            [TextChunk(text="one "), TextChunk(text="two"), TurnComplete(response=None)]
        )

        assert text == "one two"

    def test_usage_is_returned_for_the_audit_record(self):
        from omnigent import TurnComplete

        _, _, usage = self._run(
            [TurnComplete(response="x", usage={"input_tokens": 5, "output_tokens": 7})]
        )

        assert usage == {"input_tokens": 5, "output_tokens": 7}

    def test_prompt_and_system_prompt_reach_run_turn(self):
        from omnigent import TurnComplete

        executor, _, _ = self._run([TurnComplete(response="x")])

        messages, tools, system_prompt, _ = executor.calls[0]
        assert system_prompt == "s"
        assert tools == []
        assert len(messages) == 1
        assert messages[0].role == "user"
        assert messages[0].content == "p"

    def test_tool_events_are_logged(self):
        from omnigent import ToolCallComplete, ToolCallRequest, TurnComplete

        log = StringIO()
        self._run(
            [
                ToolCallRequest(name="Bash", args={"command": "ls"}),
                ToolCallComplete(name="Bash", result="a.txt"),
                TurnComplete(response="done"),
            ],
            log=log,
        )

        written = log.getvalue()
        assert "Bash" in written

    def test_empty_stream_yields_empty_text(self):
        _, text, usage = self._run([])

        assert text == ""
        assert usage == {}


@requires_omnigent
class TestExecutorResolution:
    """Only runs where omnigent is installed — proves the registry is accurate."""

    @pytest.mark.parametrize("provider", ["claude", "codex"])
    def test_registered_classes_actually_exist(self, provider):
        runner = OmnigentAgentRunner(provider=provider)

        executor_cls = runner._executor_class()

        assert executor_cls.__name__ == OMNIGENT_PROVIDER_EXECUTORS[provider].class_name

    @pytest.mark.parametrize("provider", ["claude", "codex"])
    def test_registered_classes_accept_cwd_and_model(self, provider):
        """The constructor shape _build_executor depends on."""
        import inspect

        runner = OmnigentAgentRunner(provider=provider)
        params = inspect.signature(runner._executor_class().__init__).parameters

        assert "cwd" in params
        assert "model" in params

    def test_build_executor_targets_the_workspace(self, tmp_path):
        runner = OmnigentAgentRunner(provider="claude", model="claude-sonnet-4-6")

        executor = runner._build_executor(Path(tmp_path))

        assert executor is not None


@requires_omnigent
class TestWorkspaceConfinement:
    """The opt-in path must not be a silent downgrade from vs_sandbox."""

    def test_os_env_confines_writes_to_the_workspace(self, tmp_path):
        runner = OmnigentAgentRunner(provider="claude")

        os_env = runner._build_os_env(Path(tmp_path))

        assert os_env.cwd == str(tmp_path)
        assert os_env.sandbox is not None
        assert os_env.sandbox.write_paths == [str(tmp_path)]

    def test_sandbox_is_never_disabled(self, tmp_path):
        """`type="none"` would run the agent unconfined."""
        runner = OmnigentAgentRunner(provider="codex")

        os_env = runner._build_os_env(Path(tmp_path))

        assert os_env.sandbox.type != "none"

    def test_sandbox_backend_matches_the_host_platform(self, tmp_path):
        """The dataclass default is linux_bwrap, which is wrong on macOS."""
        import sys

        runner = OmnigentAgentRunner(provider="claude")

        os_env = runner._build_os_env(Path(tmp_path))

        expected = {
            "linux": "linux_bwrap",
            "darwin": "darwin_seatbelt",
        }.get("linux" if sys.platform.startswith("linux") else sys.platform)
        if expected is not None:
            assert os_env.sandbox.type == expected

    def test_two_workspaces_do_not_share_a_grant(self, tmp_path):
        """Sibling runs must not be writable from one another."""
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        runner = OmnigentAgentRunner(provider="claude")

        env_a = runner._build_os_env(a)
        env_b = runner._build_os_env(b)

        assert env_a.sandbox.write_paths == [str(a)]
        assert env_b.sandbox.write_paths == [str(b)]
        assert str(b) not in env_a.sandbox.write_paths
