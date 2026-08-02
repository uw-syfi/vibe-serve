"""Tests for the opt-in Omnigent agent backend.

The contract these tests protect is asymmetric on purpose:

- With ``omnigent_agent_backend`` off (the default), ``build_agent_runner``
  must behave exactly as it did before the flag existed — same runner class,
  same provider resolution, same Docker handling. Those cases are the
  regression guard for the agentshim path.
- With the flag on, every unsupported combination must fail loudly and name
  the remedy, because a silent fallback to agentshim would make run logs
  misattribute which stack produced a result.

Tests that need the real ``omnigent`` package are guarded by
``requires_omnigent``. That guard is a safety net, not the expected path: the
``dev`` dependency group pulls ``vibesys[omnigent]``, so ``uv sync --dev`` —
what CI runs — installs it and these tests execute. They skip only for someone
who deliberately synced without dev dependencies.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
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
    _MODULE = "omnigent.inner.claude_sdk_executor"

    def test_import_error_names_the_extra(self, monkeypatch):
        runner = OmnigentAgentRunner(provider="claude")
        monkeypatch.setattr(
            "vibesys.agents.omnigent.runner.import_module",
            MagicMock(side_effect=ImportError("No module named 'omnigent'")),
        )

        with pytest.raises(OmnigentUnavailableError) as exc:
            runner._executor_class()

        message = str(exc.value)
        # Must name both remedies: install the extra, or turn the flag off.
        assert "--extra omnigent" in message
        assert "omnigent_agent_backend" in message
        assert "agentshim" in message
        assert self._MODULE in message

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
    reason="omnigent is an optional extra; install it with `uv sync --dev`",
)


def _sandbox_backend_available() -> bool:
    """Whether this host can actually build Omnigent's platform sandbox.

    Omnigent resolves the backend binary when an OS environment is created, so
    tests that build real ``sys_os_*`` tools need it present. GitHub's runners
    do not ship ``bwrap``. Mirrors the ``VIBESYS_REQUIRE_SANDBOX_TESTS`` escape
    hatch used by ``tests/_agent_cli/test_hostsandbox.py`` so a Linux CI job
    that does install bubblewrap can force these on.
    """
    if os.environ.get("VIBESYS_REQUIRE_SANDBOX_TESTS") == "1":
        return True
    if sys.platform.startswith("linux"):
        return shutil.which("bwrap") is not None
    if sys.platform == "darwin":
        return shutil.which("sandbox-exec") is not None
    return False


requires_sandbox_backend = pytest.mark.skipif(
    not _sandbox_backend_available(),
    reason="requires the platform sandbox backend (bwrap / sandbox-exec) "
    "(set VIBESYS_REQUIRE_SANDBOX_TESTS=1 to force)",
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
        """Messages must be plain dicts.

        ``run_turn`` is annotated ``list[Message]``, but 0.6.0's executors read
        the entries with ``.get()``. Passing the advertised dataclass raises
        ``AttributeError: 'Message' object has no attribute 'get'`` on a live
        turn, which no fake-executor test would catch.
        """
        from omnigent import TurnComplete

        executor, _, _ = self._run([TurnComplete(response="x")])

        messages, tools, system_prompt, _ = executor.calls[0]
        assert system_prompt == "s"
        assert tools == []
        assert len(messages) == 1
        assert isinstance(messages[0], dict)
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "p"

    def test_tool_schemas_are_forwarded(self):
        """An empty tool list leaves the agent with no filesystem access."""
        import asyncio

        from omnigent import TurnComplete

        from vibesys.agents.callbacks import AgentLogger
        from vibesys.agents.omnigent.runner import _drive_turn

        executor = _FakeExecutor([TurnComplete(response="x")])
        schemas = [{"name": "sys_os_read", "description": "d", "parameters": {}}]
        asyncio.run(
            _drive_turn(
                executor,
                prompt="p",
                system_prompt="s",
                logger=AgentLogger(agent_label="Judge"),
                tool_schemas=schemas,
            )
        )

        _, tools, _, _ = executor.calls[0]
        assert tools == schemas

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

    @requires_sandbox_backend
    def test_build_executor_attaches_os_tools_and_dispatcher(self, tmp_path):
        runner = OmnigentAgentRunner(provider="claude", model="claude-sonnet-4-6")

        executor, schemas = runner._build_executor(Path(tmp_path))

        assert executor is not None
        # Without these the agent has no filesystem access at all.
        assert {s["name"] for s in schemas} == {
            "sys_os_read",
            "sys_os_write",
            "sys_os_edit",
            "sys_os_shell",
        }
        assert executor._tool_executor is not None
        runner.close()


@requires_omnigent
class TestMissingSandboxBackend:
    """A host without bwrap must get the flag's error, not a bare OSError."""

    def test_missing_backend_binary_is_translated(self, tmp_path, monkeypatch):
        from vibesys.agents.omnigent.runner import _build_os_tools

        monkeypatch.setattr(
            "omnigent.inner.os_env.create_os_environment",
            MagicMock(
                side_effect=OSError("linux_bwrap sandbox requires the 'bwrap' binary on PATH.")
            ),
        )
        spec = OmnigentAgentRunner(provider="claude")._build_os_env(Path(tmp_path))

        with pytest.raises(OmnigentUnavailableError) as exc:
            _build_os_tools(spec, Path(tmp_path))

        message = str(exc.value)
        assert "omnigent_agent_backend" in message
        assert "bwrap" in message
        # Running unconfined must never be offered as the way out.
        assert "agentshim" in message
        assert "disable the flag" in message

    def test_declining_to_build_an_env_is_also_an_error(self, tmp_path, monkeypatch):
        """A None env would mean a sandboxed but toolless agent."""
        from vibesys.agents.omnigent.runner import _build_os_tools

        monkeypatch.setattr(
            "omnigent.inner.os_env.create_os_environment", MagicMock(return_value=None)
        )
        spec = OmnigentAgentRunner(provider="claude")._build_os_env(Path(tmp_path))

        with pytest.raises(OmnigentUnavailableError) as exc:
            _build_os_tools(spec, Path(tmp_path))

        assert "no file or shell tools" in str(exc.value)


@requires_omnigent
@requires_sandbox_backend
class TestOsTools:
    """Covers the tool layer a live turn proved was missing."""

    def test_schemas_are_flat_not_openai_function_shaped(self, tmp_path):
        """`run_turn` reads name/description off the top level.

        Passing `get_schema()` verbatim registers MCP tools with empty names,
        and the agent reports having no file tools.
        """
        from vibesys.agents.omnigent.runner import _build_os_tools

        spec = OmnigentAgentRunner(provider="claude")._build_os_env(Path(tmp_path))
        schemas, _ = _build_os_tools(spec, Path(tmp_path))

        for schema in schemas:
            assert schema["name"]
            assert "function" not in schema
            assert schema["parameters"]["type"] == "object"

    def test_dispatcher_executes_a_real_read(self, tmp_path):
        import asyncio

        from vibesys.agents.omnigent.runner import _build_os_tools

        (tmp_path / "NOTES.md").write_text("launch code 4417\n")
        spec = OmnigentAgentRunner(provider="claude")._build_os_env(Path(tmp_path))
        _, dispatch = _build_os_tools(spec, Path(tmp_path))

        result = asyncio.run(dispatch("sys_os_read", {"path": "NOTES.md"}))

        assert "4417" in str(result)

    def test_dispatcher_reports_unknown_tools(self):
        import asyncio

        from vibesys.agents.omnigent.runner import _build_os_tools

        with tempfile.TemporaryDirectory() as tmp:
            spec = OmnigentAgentRunner(provider="claude")._build_os_env(Path(tmp))
            _, dispatch = _build_os_tools(spec, Path(tmp))

            result = asyncio.run(dispatch("nope", {}))

        assert "unknown tool" in str(result)


@requires_omnigent
@requires_sandbox_backend
class TestLifecycle:
    def test_close_is_idempotent(self, tmp_path):
        runner = OmnigentAgentRunner(provider="claude")
        runner._build_executor(Path(tmp_path))

        runner.close()
        runner.close()

        assert runner._executors == {}
        assert runner._loop is None

    def test_turns_share_one_loop(self, tmp_path):
        """A per-turn asyncio.run would strand cached executors on a dead loop."""
        runner = OmnigentAgentRunner(provider="claude")

        async def _noop() -> str:
            return "ok"

        runner._run_async(_noop())
        first = runner._loop
        runner._run_async(_noop())

        assert runner._loop is first
        assert first is not None and not first.is_closed()
        runner.close()


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


@requires_omnigent
class TestTurnPathWithFakeExecutor:
    """Covers invoke / invoke_text / _generate / close without bwrap or creds.

    The live probe in ``experiments/omnigent-agent-backend/`` exercises these
    against real CLIs, but it needs credentials and a sandbox backend, so CI
    cannot run it. Injecting a fake executor covers the same control flow —
    logging, parsing, fallback, usage records, executor reuse, and teardown —
    on any host.
    """

    @staticmethod
    def _runner(tmp_path, events, **kwargs):
        runner = OmnigentAgentRunner(provider="claude", model_name="m", **kwargs)
        executor = _FakeExecutor(events)
        schemas = [{"name": "sys_os_read", "description": "d", "parameters": {}}]
        # Bypass _build_executor so no OS environment (and so no bwrap) is needed.
        runner._build_executor = lambda _ws: (executor, schemas)  # type: ignore[method-assign]
        return runner, executor

    def test_invoke_parses_a_structured_response(self, tmp_path):
        from omnigent import TurnComplete

        payload = '{"analysis":"a","feedback":"f","verdict":"pass"}'
        runner, _ = self._runner(tmp_path, [TurnComplete(response=payload)])

        result = runner.invoke(
            kind="judge",
            workspace=tmp_path,
            system_prompt="sys",
            user_prompt="ask",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="r1",
        )

        assert result.analysis == "a"
        assert result.verdict == Verdict.PASS
        runner.close()

    def test_invoke_falls_back_on_unparseable_output(self, tmp_path):
        from omnigent import TurnComplete

        runner, _ = self._runner(tmp_path, [TurnComplete(response="not json at all")])

        result = runner.invoke(
            kind="judge",
            workspace=tmp_path,
            system_prompt="sys",
            user_prompt="ask",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="r1",
        )

        # The contract is fallback, never raise.
        assert result.analysis == "fb"
        runner.close()

    def test_invoke_sends_the_schema_hint(self, tmp_path):
        from omnigent import TurnComplete

        runner, executor = self._runner(tmp_path, [TurnComplete(response="{}")])

        runner.invoke(
            kind="judge",
            workspace=tmp_path,
            system_prompt="sys",
            user_prompt="ask",
            response_cls=JudgeResponse,
            fallback_factory=_judge_fallback,
            round_label="r1",
        )

        messages, _, system_prompt, _ = executor.calls[0]
        assert system_prompt == "sys"
        assert "JudgeResponse" in messages[0]["content"]
        runner.close()

    def test_invoke_text_returns_the_raw_response(self, tmp_path):
        from omnigent import TurnComplete

        runner, _ = self._runner(tmp_path, [TurnComplete(response="hello there")])

        text = runner.invoke_text(
            kind="chat",
            workspace=tmp_path,
            system_prompt="sys",
            user_prompt="hi",
            round_label="r1",
        )

        assert text == "hello there"
        runner.close()

    def test_invoke_text_handles_an_empty_turn(self, tmp_path):
        from omnigent import TurnComplete

        runner, _ = self._runner(tmp_path, [TurnComplete(response=None)])

        text = runner.invoke_text(
            kind="implementer",
            workspace=tmp_path,
            system_prompt="sys",
            user_prompt="hi",
            round_label="r1",
        )

        assert text == ""
        runner.close()

    def test_env_overrides_apply_during_the_turn_and_are_restored(self, tmp_path):
        from omnigent import TurnComplete

        seen: list[str | None] = []

        class _EnvProbe(_FakeExecutor):
            def run_turn(self, messages, tools, system_prompt, config=None):
                seen.append(os.environ.get("CUDA_VISIBLE_DEVICES"))
                return super().run_turn(messages, tools, system_prompt, config)

        runner = OmnigentAgentRunner(provider="claude", model_name="m")
        executor = _EnvProbe([TurnComplete(response="ok")])
        runner._build_executor = lambda _ws: (executor, [])  # type: ignore[method-assign]
        os.environ.pop("CUDA_VISIBLE_DEVICES", None)

        runner.invoke_text(
            kind="implementer",
            workspace=tmp_path,
            system_prompt="sys",
            user_prompt="hi",
            env={"CUDA_VISIBLE_DEVICES": "2"},
            round_label="r1",
        )

        assert seen == ["2"]
        assert "CUDA_VISIBLE_DEVICES" not in os.environ
        runner.close()

    def test_usage_record_is_written_per_turn(self, tmp_path):
        from omnigent import TurnComplete

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        runner, _ = self._runner(
            tmp_path,
            [TurnComplete(response="ok", usage={"input_tokens": 3, "output_tokens": 4})],
            log_dir=log_dir,
        )

        runner.invoke_text(
            kind="implementer",
            workspace=tmp_path,
            system_prompt="sys",
            user_prompt="hi",
            round_label="r1",
        )

        record = json.loads((log_dir / "usage.jsonl").read_text().strip())
        assert record["input_tokens"] == 3
        assert record["output_tokens"] == 4
        assert record["provider"] == "claude"
        runner.close()

    def test_a_failed_turn_still_writes_its_usage_record(self, tmp_path):
        """Tokens were spent either way; an audit gap on failure defeats the point."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        class _Boom(_FakeExecutor):
            def run_turn(self, messages, tools, system_prompt, config=None):
                raise RuntimeError("harness exploded")

        runner = OmnigentAgentRunner(provider="claude", model_name="m", log_dir=log_dir)
        runner._build_executor = lambda _ws: (_Boom([]), [])  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="harness exploded"):
            runner.invoke_text(
                kind="implementer",
                workspace=tmp_path,
                system_prompt="sys",
                user_prompt="hi",
                round_label="r1",
            )

        assert (log_dir / "usage.jsonl").read_text().strip()
        runner.close()

    def test_the_run_log_records_the_backend_and_provider(self, tmp_path):
        from omnigent import TurnComplete

        log = StringIO()
        runner, _ = self._runner(tmp_path, [TurnComplete(response="ok")], run_log_file=log)

        runner.invoke_text(
            kind="implementer",
            workspace=tmp_path,
            system_prompt="sys",
            user_prompt="hi",
            round_label="r1",
        )

        written = log.getvalue()
        assert "backend: omnigent" in written
        assert "provider: claude" in written
        assert "harness: claude-sdk" in written
        runner.close()

    def test_executors_are_reused_per_kind_but_not_for_chat(self, tmp_path):
        from omnigent import TurnComplete

        built: list[str] = []

        def _build(_ws):
            built.append("x")
            return _FakeExecutor([TurnComplete(response="ok")]), []

        runner = OmnigentAgentRunner(provider="claude", model_name="m")
        runner._build_executor = _build  # type: ignore[method-assign]

        for _ in range(2):
            runner.invoke_text(
                kind="implementer",
                workspace=tmp_path,
                system_prompt="s",
                user_prompt="u",
                round_label="r",
            )
        assert len(built) == 1, "implementer executor should be cached"

        for _ in range(2):
            runner.invoke_text(
                kind="chat", workspace=tmp_path, system_prompt="s", user_prompt="u", round_label="r"
            )
        assert len(built) == 3, "chat must start a fresh session each turn"
        runner.close()

    def test_explicit_session_keys_reuse_and_fresh_sessions_close(self, tmp_path):
        from omnigent import TurnComplete

        built: list[_FakeExecutor] = []
        closed: list[_FakeExecutor] = []

        class _Closable(_FakeExecutor):
            async def close(self):
                closed.append(self)

        def _build(_ws):
            executor = _Closable([TurnComplete(response="ok")])
            built.append(executor)
            return executor, []

        runner = OmnigentAgentRunner(provider="claude", model_name="m")
        runner._build_executor = _build  # type: ignore[method-assign]

        for _ in range(2):
            runner.invoke_text(
                kind="implementer",
                workspace=tmp_path,
                system_prompt="s",
                user_prompt="u",
                round_label="persistent",
                reuse_session=True,
                session_key="hypothesis:a",
            )
        assert len(built) == 1
        assert closed == []

        runner.invoke_text(
            kind="judge",
            workspace=tmp_path,
            system_prompt="s",
            user_prompt="u",
            round_label="fresh",
            reuse_session=False,
        )
        assert len(built) == 2
        assert closed == [built[1]]

        runner.close()
        assert closed == [built[1], built[0]]

    def test_close_awaits_executor_close(self, tmp_path):
        from omnigent import TurnComplete

        closed: list[bool] = []

        class _Closable(_FakeExecutor):
            async def close(self):
                closed.append(True)

        runner = OmnigentAgentRunner(provider="claude", model_name="m")
        runner._build_executor = lambda _ws: (_Closable([TurnComplete(response="ok")]), [])  # type: ignore[method-assign]
        runner.invoke_text(
            kind="implementer",
            workspace=tmp_path,
            system_prompt="s",
            user_prompt="u",
            round_label="r",
        )

        runner.close()

        assert closed == [True]

    def test_close_survives_a_failing_executor_close(self, tmp_path):
        from omnigent import TurnComplete

        class _BadClose(_FakeExecutor):
            async def close(self):
                raise RuntimeError("close blew up")

        log = StringIO()
        runner = OmnigentAgentRunner(provider="claude", model_name="m", run_log_file=log)
        runner._build_executor = lambda _ws: (_BadClose([TurnComplete(response="ok")]), [])  # type: ignore[method-assign]
        runner.invoke_text(
            kind="implementer",
            workspace=tmp_path,
            system_prompt="s",
            user_prompt="u",
            round_label="r",
        )

        runner.close()  # must not raise

        assert "executor close failed" in log.getvalue()
        assert runner._loop is None


@requires_omnigent
class TestLazyPackageExports:
    def test_runner_symbols_resolve_through_package_getattr(self):
        import vibesys.agents.omnigent as pkg

        assert pkg.OmnigentAgentRunner is OmnigentAgentRunner
        assert pkg.OmnigentUnavailableError is OmnigentUnavailableError

    def test_unknown_attribute_still_raises(self):
        import vibesys.agents.omnigent as pkg

        with pytest.raises(AttributeError, match="no attribute"):
            _ = pkg.NoSuchThing


@requires_omnigent
class TestToolExecutorSeam:
    """Guards the one private Omnigent attribute this integration depends on.

    There is no public setter and it is absent from the ``Executor`` ABC, so an
    upgrade could move it. Left unguarded that degrades silently: the
    assignment would create a dead attribute, Omnigent would read ``None``, and
    every tool call would return ``{"error": "No tool executor ..."}`` — an
    agent that looks equipped but fails every action mid-run.
    """

    # Provider -> the CLI binary its executor needs at construction time.
    # CodexExecutor raises ImportError without it; ClaudeSDKExecutor falls back
    # to the SDK's bundled CLI, so it constructs anywhere.
    _PROVIDER_BINARY = {"claude": None, "codex": "codex"}

    @pytest.mark.parametrize("provider", ["claude", "codex"])
    def test_the_seam_exists_on_the_pinned_version(self, provider):
        """Fails loudly if a version bump moves the attribute."""
        from vibesys.agents.omnigent.runner import _TOOL_EXECUTOR_ATTR

        binary = self._PROVIDER_BINARY[provider]
        if binary is not None and shutil.which(binary) is None:
            pytest.skip(f"{provider} executor needs the {binary!r} CLI to construct")

        runner = OmnigentAgentRunner(provider=provider)
        executor_cls = runner._executor_class()

        # Constructed without os_env so no sandbox backend is needed here.
        executor = executor_cls(cwd=".", model=None)

        assert hasattr(executor, _TOOL_EXECUTOR_ATTR), (
            f"{executor_cls.__name__} no longer exposes {_TOOL_EXECUTOR_ATTR!r}; "
            "the Omnigent backend can no longer give agents their tools"
        )

    def test_a_missing_provider_cli_is_attributed_to_the_flag(self, tmp_path, monkeypatch):
        """Omnigent reports a missing CLI as ImportError from the constructor."""

        class _NeedsCli:
            def __init__(self, **_kwargs):
                raise ImportError("CodexExecutor requires the 'codex' CLI on PATH.")

        runner = OmnigentAgentRunner(provider="codex")
        monkeypatch.setattr(runner, "_executor_class", lambda: _NeedsCli)

        with pytest.raises(OmnigentUnavailableError) as exc:
            runner._build_executor(Path(tmp_path))

        message = str(exc.value)
        assert "omnigent_agent_backend" in message
        assert "codex" in message
        # Omnigent's own wording is preserved rather than discarded.
        assert "CLI on PATH" in message
        assert "agentshim" in message

    def test_a_moved_seam_fails_at_construction(self, tmp_path, monkeypatch):
        from vibesys.agents.omnigent import runner as runner_mod

        class _Renamed:
            """An executor whose dispatch slot has been renamed upstream."""

            def __init__(self, **_kwargs):
                self._dispatch_callback = None  # not _tool_executor

        runner = OmnigentAgentRunner(provider="claude")
        monkeypatch.setattr(runner, "_executor_class", lambda: _Renamed)
        monkeypatch.setattr(
            runner_mod, "_build_os_tools", MagicMock(side_effect=AssertionError("unreachable"))
        )

        with pytest.raises(OmnigentUnavailableError) as exc:
            runner._build_executor(Path(tmp_path))

        message = str(exc.value)
        assert "_tool_executor" in message
        assert "omnigent_agent_backend" in message
        assert "agentshim" in message
