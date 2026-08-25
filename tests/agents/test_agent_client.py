from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from vibesys.agents.client import AgentClient
from vibesys.agents.contracts import (
    AgentCapabilities,
    AgentEvent,
    AgentEventKind,
    AgentExecutionPolicy,
    AgentObserver,
    AgentSession,
    AgentSessionSpec,
    AgentTurnRequest,
    AgentTurnResult,
    AgentUsage,
    SessionDisposition,
)


class _Response(BaseModel):
    answer: str


@dataclass
class _FakeSession:
    results: list[AgentTurnResult]
    turns: list[AgentTurnRequest] = field(default_factory=list)
    close_calls: int = 0
    error: BaseException | None = None
    observers: list[AgentObserver | None] = field(default_factory=list)

    def run_turn(
        self,
        request: AgentTurnRequest,
        observer: AgentObserver | None = None,
    ) -> AgentTurnResult:
        self.turns.append(request)
        self.observers.append(observer)
        if self.error is not None:
            raise self.error
        return self.results.pop(0)

    def close(self) -> None:
        self.close_calls += 1


@dataclass
class _FakeDriver:
    queued_sessions: list[_FakeSession]
    specs: list[AgentSessionSpec] = field(default_factory=list)
    close_calls: int = 0

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities()

    def create_session(self, spec: AgentSessionSpec) -> AgentSession:
        self.specs.append(spec)
        return self.queued_sessions.pop(0)

    def close(self) -> None:
        self.close_calls += 1


def _spec(
    *,
    model: str = "model",
    workspace: Path = Path("/workspace"),
    skills: tuple[Path, ...] = (),
) -> AgentSessionSpec:
    return AgentSessionSpec(
        role="implementer",
        provider="codex",
        model=model,
        workspace=workspace,
        policy=AgentExecutionPolicy(),
        skills=skills,
    )


def test_keyed_turns_reuse_a_session() -> None:
    session = _FakeSession(
        results=[AgentTurnResult("first"), AgentTurnResult("second")],
    )
    driver = _FakeDriver([session])
    client = AgentClient(driver)

    assert (
        client.run(session_spec=_spec(), turn=AgentTurnRequest("one"), session_key="impl").text
        == "first"
    )
    assert (
        client.run(session_spec=_spec(), turn=AgentTurnRequest("two"), session_key="impl").text
        == "second"
    )

    assert len(driver.specs) == 1
    assert session.turns == [AgentTurnRequest("one"), AgentTurnRequest("two")]


def test_session_setup_materializes_skills_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(results=[AgentTurnResult("first"), AgentTurnResult("second")])
    client = AgentClient(_FakeDriver([session]))
    skill = tmp_path / "source-skill"
    calls: list[tuple[Path, list[Path]]] = []
    monkeypatch.setattr(
        "vibesys.agents.client.materialize_skills",
        lambda workspace, skills, **_kwargs: calls.append((workspace, skills)),
    )
    spec = _spec(workspace=tmp_path, skills=(skill,))

    client.run(session_spec=spec, turn=AgentTurnRequest("one"), session_key="impl")
    client.run(session_spec=spec, turn=AgentTurnRequest("two"), session_key="impl")

    assert calls == [(tmp_path, [skill])]


def test_client_forwards_observer_to_session() -> None:
    session = _FakeSession(results=[AgentTurnResult("done")])
    client = AgentClient(_FakeDriver([session]))

    @dataclass
    class Observer:
        events: list[AgentEvent] = field(default_factory=list)

        def on_event(self, event: AgentEvent) -> None:
            self.events.append(event)

    observer = Observer()
    client.run(
        session_spec=_spec(),
        turn=AgentTurnRequest("one"),
        observer=observer,
    )

    assert session.observers == [observer]
    observer.on_event(AgentEvent(AgentEventKind.TEXT, text="chunk"))
    assert observer.events == [AgentEvent(AgentEventKind.TEXT, text="chunk")]


def test_changed_session_spec_closes_and_replaces_cached_session() -> None:
    old = _FakeSession(results=[AgentTurnResult("old")])
    new = _FakeSession(results=[AgentTurnResult("new")])
    driver = _FakeDriver([old, new])
    client = AgentClient(driver)

    client.run(session_spec=_spec(), turn=AgentTurnRequest("one"), session_key="impl")
    result = client.run(
        session_spec=_spec(model="changed"),
        turn=AgentTurnRequest("two"),
        session_key="impl",
    )

    assert result.text == "new"
    assert old.close_calls == 1
    assert len(driver.specs) == 2


def test_unkeyed_turn_always_closes_ephemeral_session() -> None:
    session = _FakeSession(results=[AgentTurnResult("done")])
    client = AgentClient(_FakeDriver([session]))

    result = client.run(session_spec=_spec(), turn=AgentTurnRequest("one"))

    assert result.text == "done"
    assert session.close_calls == 1


@pytest.mark.parametrize(
    "result",
    [AgentTurnResult("reset", disposition=SessionDisposition.RESET_REQUIRED)],
)
def test_reset_disposition_evicts_session(result: AgentTurnResult) -> None:
    first = _FakeSession(results=[result])
    second = _FakeSession(results=[AgentTurnResult("recovered")])
    driver = _FakeDriver([first, second])
    client = AgentClient(driver)

    client.run(session_spec=_spec(), turn=AgentTurnRequest("one"), session_key="impl")
    client.run(session_spec=_spec(), turn=AgentTurnRequest("two"), session_key="impl")

    assert first.close_calls == 1
    assert len(driver.specs) == 2


def test_turn_exception_evicts_session() -> None:
    failed = _FakeSession(results=[], error=ValueError("failed"))
    recovered = _FakeSession(results=[AgentTurnResult("ok")])
    driver = _FakeDriver([failed, recovered])
    client = AgentClient(driver)

    with pytest.raises(ValueError, match="failed"):
        client.run(session_spec=_spec(), turn=AgentTurnRequest("one"), session_key="impl")
    result = client.run(session_spec=_spec(), turn=AgentTurnRequest("two"), session_key="impl")

    assert failed.close_calls == 1
    assert result.text == "ok"


def test_close_is_idempotent_and_rejects_future_turns() -> None:
    session = _FakeSession(results=[AgentTurnResult("ok")])
    driver = _FakeDriver([session])
    client = AgentClient(driver)
    client.run(session_spec=_spec(), turn=AgentTurnRequest("one"), session_key="impl")

    client.close()
    client.close()

    assert session.close_calls == 1
    assert driver.close_calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        client.run(session_spec=_spec(), turn=AgentTurnRequest("two"))


def test_invoke_builds_session_and_turn_contracts_and_records_usage(tmp_path: Path) -> None:
    usage = AgentUsage(input_tokens=12, output_tokens=4, total_cost_usd=0.02, duration_ms=30)
    session = _FakeSession(results=[AgentTurnResult('{"answer":"done"}', usage=usage)])
    driver = _FakeDriver([session])
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    client = AgentClient(
        driver,
        provider="codex",
        model_name="gpt-test",
        timeout=45,
        log_dir=log_dir,
        role_models={"judge": "gpt-judge"},
        role_reasoning_efforts={"judge": "high"},
    )

    response = client.invoke(
        kind="judge",
        workspace=tmp_path,
        system_prompt="system",
        user_prompt="user",
        response_cls=_Response,
        fallback_factory=lambda: _Response(answer="fallback"),
        round_label="judge #1",
        env={"VISIBLE": "1"},
        session_key="review",
    )

    assert response == _Response(answer="done")
    assert driver.specs[0].model == "gpt-judge"
    assert driver.specs[0].reasoning_effort == "high"
    assert driver.specs[0].environment == (("VISIBLE", "1"),)
    assert session.turns[0].instructions == "system"
    assert session.turns[0].message == "user"
    assert session.turns[0].output_schema is _Response
    assert session.turns[0].timeout == timedelta(seconds=45)
    record = json.loads((log_dir / "usage.jsonl").read_text())
    expected = {
        "kind": "judge",
        "round_label": "judge #1",
        "provider": "codex",
        "model": "gpt-judge",
        "reasoning_effort": "high",
        "input_tokens": 12,
        "output_tokens": 4,
        "total_cost_usd": 0.02,
        "duration_ms": 30,
    }
    assert {key: record[key] for key in expected} == expected


def test_invoke_uses_fallback_only_for_unparseable_output(tmp_path: Path) -> None:
    session = _FakeSession(results=[AgentTurnResult("not json")])
    client = AgentClient(_FakeDriver([session]))
    fallback_calls = 0

    def fallback() -> _Response:
        nonlocal fallback_calls
        fallback_calls += 1
        return _Response(answer="fallback")

    response = client.invoke(
        kind="judge",
        workspace=tmp_path,
        system_prompt="system",
        user_prompt="user",
        response_cls=_Response,
        fallback_factory=fallback,
        round_label="judge #1",
    )

    assert response == _Response(answer="fallback")
    assert fallback_calls == 1
