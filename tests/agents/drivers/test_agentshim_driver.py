from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from vibesys.agents.contracts import (
    AgentEvent,
    AgentExecutionPolicy,
    AgentSessionSpec,
    AgentTurnRequest,
    MCPServerSpec,
)
from vibesys.agents.drivers import agentshim as subject
from vs_sandbox import ProjectPathPolicy

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class _Observer:
    events: list[AgentEvent] = field(default_factory=list)

    def on_event(self, event: AgentEvent) -> None:
        self.events.append(event)


class _FakeAgent:
    supports_native_output_schema = False
    native_output_schema_allows_arbitrary_keys = False
    native_output_schema_wants_absolute_path = False

    def __init__(
        self,
        model: str | None = None,
        event_handler: Any | None = None,  # noqa: ANN401  # tracked: #288
        *,
        executor: Any | None = None,  # noqa: ANN401  # tracked: #288
    ) -> None:
        self.model = model
        self.event_handler = event_handler
        self.executor = executor
        self.env = {"BASE": "one"}
        self.binary_path = "/bin/fake"
        self.sandbox = None
        self.session_id = "session-1"
        self.generate_calls: list[tuple[str, str | None, int | None]] = []
        self.install_calls: list[tuple[Path, list[Any]]] = []
        self.uninstall_calls: list[tuple[Path, list[Any]]] = []
        self.output_schema_paths: list[str | None] = []
        self.reasoning_effort: str | None = None
        self.error: BaseException | None = None
        self._last_session = SimpleNamespace(
            final_usage={"input_tokens": 12, "output_tokens": 3},
            total_cost_usd=0.25,
            duration_ms=90,
        )

    def set_reasoning_effort(self, effort: str) -> None:
        self.reasoning_effort = effort

    def set_output_schema_path(self, path: str | None) -> None:
        self.output_schema_paths.append(path)

    def install_mcp_servers(self, workspace: Path, servers: list[Any]) -> None:
        self.install_calls.append((workspace, servers))

    def uninstall_mcp_servers(self, workspace: Path, servers: list[Any]) -> None:
        self.uninstall_calls.append((workspace, servers))

    def generate(
        self,
        prompt: str,
        *,
        cwd: str | None,
        timeout: int | None,
        silent: bool,
    ) -> str:
        assert silent
        assert self.event_handler is not None
        self.generate_calls.append((prompt, cwd, timeout))
        self.event_handler.on_thinking("working")
        self.event_handler.on_usage({"input_tokens": 12, "output_tokens": 3})
        if self.error is not None:
            raise self.error
        return "done"


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> list[_FakeAgent]:
    built: list[_FakeAgent] = []

    def factory(
        model: str | None = None,
        event_handler: Any | None = None,  # noqa: ANN401  # tracked: #288
        *,
        executor: Any | None = None,  # noqa: ANN401  # tracked: #288
    ) -> _FakeAgent:
        agent = _FakeAgent(model, event_handler, executor=executor)
        built.append(agent)
        return agent

    monkeypatch.setitem(subject._PROVIDER_CLASSES, "codex", factory)  # noqa: SLF001
    monkeypatch.setattr(subject, "declare_agent_host_resources", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(subject, "build_host_sandbox", lambda *_args, **_kwargs: "sandbox")
    return built


def _spec(tmp_path: Path, **changes: Any) -> AgentSessionSpec:  # noqa: ANN401
    values: dict[str, Any] = {
        "role": "implementer",
        "provider": "codex",
        "workspace": tmp_path,
        "model": "gpt-test",
        "policy": AgentExecutionPolicy(require_enforcement=True),
        "environment": (("GPU", "0"),),
        "reasoning_effort": "high",
    }
    values.update(changes)
    return AgentSessionSpec(**values)


def test_session_setup_applies_policy_model_environment_and_reasoning(
    fake_agent: list[_FakeAgent],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = ProjectPathPolicy(read_only_paths=("OBJECTIVE.md",))
    captured: dict[str, Any] = {}

    def build_sandbox(workspace: Path, **kwargs: Any) -> str:  # noqa: ANN401
        captured.update(workspace=workspace, **kwargs)
        return "confined"

    monkeypatch.setattr(subject, "build_host_sandbox", build_sandbox)
    driver = subject.AgentShimDriver(provider="codex")
    driver.create_session(
        _spec(
            tmp_path,
            policy=AgentExecutionPolicy(project_paths=policy, require_enforcement=True),
        )
    )

    agent = fake_agent[0]
    assert agent.model == "gpt-test"
    assert agent.env == {"BASE": "one", "GPU": "0"}
    assert agent.reasoning_effort == "high"
    assert agent.sandbox == "confined"
    assert captured["workspace"] == tmp_path
    assert captured["project_path_policy"] is policy
    assert captured["require_enforcement"] is True


def test_turn_forwards_prompt_timeout_events_and_usage(
    fake_agent: list[_FakeAgent], tmp_path: Path
) -> None:
    driver = subject.AgentShimDriver(provider="codex", timeout=30)
    session = driver.create_session(_spec(tmp_path))
    observer = _Observer()

    result = session.run_turn(
        AgentTurnRequest(
            message="Do it",
            instructions="Follow these rules",
            timeout=timedelta(seconds=7),
        ),
        observer,
    )

    assert result.text == "done"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 3
    assert result.usage.total_cost_usd == 0.25
    assert result.provider_session_id == "session-1"
    assert fake_agent[0].generate_calls == [("Follow these rules\n\nDo it", str(tmp_path), 7)]
    assert [event.kind for event in observer.events] == [
        subject.AgentEventKind.THINKING,
        subject.AgentEventKind.USAGE,
    ]


def test_mcp_is_session_scoped_but_activated_only_around_turn(
    fake_agent: list[_FakeAgent], tmp_path: Path
) -> None:
    server = MCPServerSpec(name="issues", command="python", args=("-m", "issues"))
    session = subject.AgentShimDriver(provider="codex").create_session(
        _spec(tmp_path, mcp_servers=(server,))
    )

    session.run_turn(AgentTurnRequest(message="review"))

    installed = fake_agent[0].install_calls[0][1][0]
    assert installed.name == "issues"
    assert installed.command == subject.sys.executable
    assert installed.args == ["-m", "issues"]
    assert fake_agent[0].uninstall_calls == fake_agent[0].install_calls


def test_mcp_cleanup_preserves_original_turn_error(
    fake_agent: list[_FakeAgent], tmp_path: Path
) -> None:
    session = subject.AgentShimDriver(provider="codex").create_session(
        _spec(tmp_path, mcp_servers=(MCPServerSpec(name="issues", command="server"),))
    )
    fake_agent[0].error = ValueError("turn failed")

    def fail_cleanup(_workspace: Path, _servers: list[Any]) -> None:
        raise OSError("cleanup failed")  # noqa: TRY003  # tracked: #288

    fake_agent[0].uninstall_mcp_servers = fail_cleanup

    with pytest.raises(ValueError, match="turn failed"):
        session.run_turn(AgentTurnRequest(message="review"))


def test_driver_and_session_close_are_idempotent(
    fake_agent: list[_FakeAgent], tmp_path: Path
) -> None:
    assert fake_agent == []
    driver = subject.AgentShimDriver(provider="codex")
    session = driver.create_session(_spec(tmp_path))

    session.close()
    session.close()
    driver.close()
    driver.close()

    with pytest.raises(RuntimeError, match="closed"):
        session.run_turn(AgentTurnRequest(message="later"))
    with pytest.raises(RuntimeError, match="closed"):
        driver.create_session(_spec(tmp_path))
