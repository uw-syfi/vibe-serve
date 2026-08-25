"""Focused tests for the Omnigent agent-driver adapter."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from vibesys.agents.contracts import (
    AgentEvent,
    AgentExecutionPolicy,
    AgentSessionSpec,
    AgentTurnRequest,
    MCPServerSpec,
)
from vibesys.agents.drivers.omnigent import (
    OmnigentDriver,
    OmnigentDriverError,
    OmnigentSession,
)
from vibesys.schemas import JudgeResponse
from vs_sandbox import HostResource, ProjectPathPolicy

omnigent = pytest.importorskip("omnigent")
TextChunk = omnigent.TextChunk
ToolCallComplete = omnigent.ToolCallComplete
ToolCallRequest = omnigent.ToolCallRequest
TurnComplete = omnigent.TurnComplete


class _FakeExecutor:
    def __init__(self, events: list[Any], *, delay: float = 0) -> None:
        self.events = events
        self.delay = delay
        self.calls: list[tuple[Any, ...]] = []
        self.close_calls = 0
        self.thread_id = "provider-session"
        self._tool_executor = None

    def run_turn(self, messages, tools, instructions, config=None):  # noqa: ANN001, ANN202
        self.calls.append((messages, tools, instructions, config, os.environ.get("DRIVER_TEST")))

        async def stream():  # noqa: ANN202
            if self.delay:
                await asyncio.sleep(self.delay)
            for event in self.events:
                yield event

        return stream()

    async def close(self) -> None:
        self.close_calls += 1


@dataclass
class _Observer:
    events: list[AgentEvent] = field(default_factory=list)

    def on_event(self, event: AgentEvent) -> None:
        self.events.append(event)


def _spec(tmp_path: Path, **changes: Any) -> AgentSessionSpec:  # noqa: ANN401
    base = AgentSessionSpec(
        role="judge",
        provider="codex",
        workspace=tmp_path,
        model="gpt-5.5",
        policy=AgentExecutionPolicy(),
    )
    return replace(base, **changes)


def _session(tmp_path: Path, executor: _FakeExecutor) -> tuple[OmnigentDriver, OmnigentSession]:
    driver = OmnigentDriver()
    session = OmnigentSession(
        driver=driver,
        spec=_spec(tmp_path, reasoning_effort="high", environment=(("DRIVER_TEST", "set"),)),
        executor=executor,
        tool_schemas=[{"name": "sys_os_read"}],
    )
    driver._sessions.add(session)  # noqa: SLF001
    return driver, session


def test_turn_normalizes_events_usage_schema_and_session_id(tmp_path: Path) -> None:
    executor = _FakeExecutor(
        [
            TextChunk("partial"),
            ToolCallRequest(name="Read", args={"path": "a"}),
            ToolCallComplete(name="Read", result="contents"),
            TurnComplete(response="final", usage={"input_tokens": 3, "output_tokens": 5}),
        ]
    )
    driver, session = _session(tmp_path, executor)
    observer = _Observer()

    result = session.run_turn(
        AgentTurnRequest(
            "answer",
            instructions="system",
            output_schema=JudgeResponse,
        ),
        observer,
    )

    assert result.text == "final"
    assert result.usage.input_tokens == 3
    assert result.usage.output_tokens == 5
    assert result.provider_session_id == "provider-session"
    messages, tools, instructions, config, environment = executor.calls[0]
    assert messages[0]["content"].startswith("answer")
    assert "JudgeResponse" in messages[0]["content"]
    assert tools == [{"name": "sys_os_read"}]
    assert instructions == "system"
    assert config.extra["reasoning_effort"] == "high"
    assert environment == "set"
    assert "DRIVER_TEST" not in os.environ
    assert [event.kind.value for event in observer.events] == [
        "text",
        "tool_call",
        "tool_result",
        "usage",
    ]
    driver.close()


def test_turn_complete_response_wins_and_chunks_are_fallback(tmp_path: Path) -> None:
    final_executor = _FakeExecutor([TextChunk("chunk"), TurnComplete(response="final")])
    final_driver, final_session = _session(tmp_path, final_executor)
    assert final_session.run_turn(AgentTurnRequest("one")).text == "final"
    final_driver.close()

    chunk_executor = _FakeExecutor([TextChunk("one "), TextChunk("two")])
    chunk_driver, chunk_session = _session(tmp_path, chunk_executor)
    assert chunk_session.run_turn(AgentTurnRequest("two")).text == "one two"
    chunk_driver.close()


def test_timeout_poisons_session_and_cleanup_is_idempotent(tmp_path: Path) -> None:
    executor = _FakeExecutor([], delay=0.1)
    driver, session = _session(tmp_path, executor)

    with pytest.raises(TimeoutError):
        session.run_turn(AgentTurnRequest("slow", timeout=timedelta(milliseconds=1)))
    with pytest.raises(RuntimeError, match="must be reset"):
        session.run_turn(AgentTurnRequest("again"))

    session.close()
    session.close()
    driver.close()
    assert executor.close_calls == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"mcp_servers": (MCPServerSpec("issues", "python"),)}, "MCP"),
        (
            {
                "policy": AgentExecutionPolicy(
                    host_resources=(HostResource(Path("model")),),
                )
            },
            "host-resource",
        ),
        (
            {
                "policy": AgentExecutionPolicy(
                    project_paths=ProjectPathPolicy(read_only_paths=("src/protected",)),
                )
            },
            "top-level",
        ),
        ({"policy": AgentExecutionPolicy(containerized=True)}, "container"),
    ],
)
def test_create_session_rejects_unsupported_requirements_before_building(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
    message: str,
) -> None:
    driver = OmnigentDriver()

    def build(_spec: AgentSessionSpec) -> tuple[Any, list[dict[str, Any]]]:
        pytest.fail("executor must not be built")

    monkeypatch.setattr(driver, "_build_executor", build)

    with pytest.raises(OmnigentDriverError, match=message):
        driver.create_session(_spec(tmp_path, **change))


def test_create_session_accepts_supported_top_level_project_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = OmnigentDriver()
    executor = _FakeExecutor([])
    monkeypatch.setattr(driver, "_build_executor", lambda _spec: (executor, []))
    policy = ProjectPathPolicy(
        read_only_paths=(".git", ".vibesys"),
        hidden_paths=(".env",),
    )

    session = driver.create_session(
        _spec(tmp_path, policy=AgentExecutionPolicy(project_paths=policy))
    )

    session.close()
    driver.close()


def test_create_session_rejects_non_dot_hidden_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = OmnigentDriver()
    monkeypatch.setattr(
        driver,
        "_build_executor",
        lambda _spec: pytest.fail("executor must not be built"),
    )
    policy = ProjectPathPolicy(hidden_paths=("agent.toml",))

    with pytest.raises(OmnigentDriverError, match=r"agent\.toml"):
        driver.create_session(_spec(tmp_path, policy=AgentExecutionPolicy(project_paths=policy)))


def test_driver_owns_session_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _FakeExecutor([])
    driver = OmnigentDriver()
    monkeypatch.setattr(driver, "_build_executor", lambda _spec: (executor, []))

    driver.create_session(_spec(tmp_path))
    driver.close()
    driver.close()

    assert executor.close_calls == 1


def test_missing_private_tool_executor_seam_fails_during_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExecutorWithoutSeam:
        close_calls = 0

        def __init__(self, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            self.close_calls += 1

    driver = OmnigentDriver()
    monkeypatch.setattr(driver, "_executor_class", lambda _spec: ExecutorWithoutSeam)
    monkeypatch.setattr(driver, "_build_os_env", lambda _workspace: object())

    with pytest.raises(OmnigentDriverError, match="_tool_executor"):
        driver._build_executor(_spec(tmp_path))  # noqa: SLF001


def test_os_policy_is_always_sandboxed_and_workspace_scoped(tmp_path: Path) -> None:
    driver = OmnigentDriver()

    spec = driver._build_os_env(_spec(tmp_path))  # noqa: SLF001

    assert spec.sandbox is not None
    assert spec.sandbox.type != "none"
    assert spec.sandbox.write_paths == [str(tmp_path)]
    assert spec.cwd == str(tmp_path)


def test_os_policy_exposes_control_dotdirs_and_keeps_hidden_dotfiles_masked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".vibesys").mkdir()
    (tmp_path / ".codex-tmp").mkdir()
    (tmp_path / ".env").write_text("secret", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "Cargo.toml").write_text("[package]", encoding="utf-8")
    toolchain = tmp_path.parent / "toolchain"
    toolchain.mkdir(exist_ok=True)
    policy = ProjectPathPolicy(
        read_only_paths=(".git", ".vibesys"),
        hidden_paths=(".env",),
    )
    driver = OmnigentDriver()
    monkeypatch.setattr(
        "vibesys.agents.drivers.omnigent.declare_active_rust_toolchain_resources",
        lambda *_args, **_kwargs: (HostResource(toolchain, purpose="Rust toolchain"),),
    )

    spec = driver._build_os_env(  # noqa: SLF001
        _spec(tmp_path, policy=AgentExecutionPolicy(project_paths=policy))
    )

    assert spec.sandbox is not None
    assert spec.sandbox.write_paths == [str(tmp_path)]
    assert spec.sandbox.write_files is None
    assert spec.sandbox.read_paths == sorted((str(tmp_path), str(toolchain)))
    assert set(spec.sandbox.cwd_allow_hidden or ()) == {".git", ".vibesys"}


def test_codex_executor_disables_native_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Executor(_FakeExecutor):
        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
            captured.update(kwargs)
            super().__init__([])

    driver = OmnigentDriver()
    monkeypatch.setattr(driver, "_executor_class", lambda _spec: Executor)
    monkeypatch.setattr(driver, "_build_os_env", lambda _spec: object())
    rust_sysroot = tmp_path / "rust"
    monkeypatch.setattr(
        "vibesys.agents.drivers.omnigent.resolve_active_rust_toolchain",
        lambda _context, *, workspace: (rust_sysroot, workspace / "lib"),
    )

    def build_tools(
        _os_env: object, _workspace: Path, environment: dict[str, str]
    ) -> tuple[list[dict[str, Any]], object]:
        captured["tool_environment"] = environment
        return [], lambda _name, _args: None

    monkeypatch.setattr(
        "vibesys.agents.drivers.omnigent._build_os_tools",
        build_tools,
    )

    executor, _schemas = driver._build_executor(_spec(tmp_path))  # noqa: SLF001

    assert captured["disable_native_tools"] is True
    assert str(captured["tool_environment"]["PATH"]).split(os.pathsep)[0] == str(
        rust_sysroot / "bin"
    )
    assert captured["tool_environment"]["CARGO_HOME"] == str(
        tmp_path / "target" / "vibesys-cargo-home"
    )
    driver.close_executor(executor)
