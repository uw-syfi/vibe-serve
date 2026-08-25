import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from vibesys.context import _RunContext
from vibesys.errors import ConfigurationDiagnostic, ConfigurationError
from vibesys.run import RunPaths
from vibesys.server import (
    EventType,
    RunInspector,
    RunSupervisor,
)
from vibesys.server.diagnostics import (
    DiagnosticRetryability,
    DiagnosticScope,
    DiagnosticSeverity,
    exception_to_diagnostic,
)
from vibesys.server.events import (
    ConfigurationFailedData,
    EventStatus,
    JudgeResultData,
    PhaseData,
    RoundFinishedData,
)
from vibesys.server.protocol import (
    ChatQuery,
    EventsQuery,
    HistoryQuery,
    PauseCommand,
    PerformanceQuery,
    ResumeCommand,
    SnapshotQuery,
    SteerCommand,
    SubscribeRequest,
)
from vibesys.server.runtime import run_server
from vibesys.server.schema import ProtocolDocument
from vibesys.server.service import SupervisionService
from vibesys.server.transport import SupervisionSocketServer
from vs_loop_state import RoundRecord
from vs_project import AgentRunConfiguration, Project, RunEnvironmentRecord


def _events(path):  # noqa: ANN001, ANN202  # tracked: #288
    return [json.loads(line) for line in path.read_text().splitlines()]


def _project_run(project: Path) -> tuple[Project, str]:
    project.mkdir()
    (project / "OBJECTIVE.md").write_text("Make the queue fast.\n", encoding="utf-8")
    vibesys_project = Project.open(project)
    store = vibesys_project.state
    store.create_project("queue")
    manifest = store.new_run_manifest(
        "queue",
        run_id="queue-run",
        branch="vibesys/queue-run",
        vibesys_version="0.2.0-test",
        configuration=AgentRunConfiguration(
            outer_loop="agent",
            run_environment=RunEnvironmentRecord(name="local"),
            inner_loop="single-agent",
            interface="inprocess",
            agent_backend="stub",
            compute_backend="cpu",
            profiler="none",
            max_rounds=3,
            max_retries_per_round=1,
            judge_every=1,
            official_eval_every=1,
            memory_layout="files",
        ),
        trusted_input_baseline="0" * 40,
    )
    store.create_run(manifest)
    return vibesys_project, manifest.run_id


def _round(
    number: int,
    *,
    metric: float | None,
    passed: bool,
    reason: str | None = None,
) -> RoundRecord:
    return RoundRecord(
        round_number=number,
        commit=f"{number:x}" * 40,
        perf_metric=metric,
        perf_unit="total_ops_per_sec" if metric is not None else None,
        passed=passed,
        profile_skipped=metric is None,
        official_evaluation_reason=reason,
    )


def test_chat_is_audited_but_not_injected(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.record(EventType.CHAT, "What is happening?", status="answered")
    supervisor.before_agent("judge", "round 2", "original prompt")
    started = next(
        event for event in supervisor.read_events() if event.type == "invocation_started"
    )
    assert started.data.user_prompt == "original prompt"  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297
    event_types = [event["type"] for event in _events(tmp_path / "run-events.jsonl")]
    assert event_types.index("chat") < event_types.index("invocation_started")


def test_pause_takes_effect_at_next_safe_point(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.pause_after_call()
    supervisor.after_agent("implementer", "round 1")
    result = []
    waiter = threading.Thread(
        target=lambda: result.append(supervisor.before_agent("judge", "round 1", "prompt"))
    )
    waiter.start()
    time.sleep(0.02)
    assert waiter.is_alive()
    supervisor.resume()
    waiter.join(timeout=1)
    # before_agent returns the effective prompt (unchanged without steering).
    assert result == ["prompt"]


def test_steer_injects_into_next_invocation(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.steer("focus on the KV cache")

    effective = supervisor.before_agent("implementer", "round 1", "Do the work")

    assert "Do the work" in effective
    assert "focus on the KV cache" in effective
    assert "Operator steering" in effective
    started = next(e for e in supervisor.read_events() if e.type == "invocation_started")
    assert started.data.user_prompt == effective  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297
    # Steering is one-shot: a later invocation without a new /steer is unchanged.
    supervisor.after_agent("implementer", "round 1")
    next_effective = supervisor.before_agent("judge", "round 1", "Review it")
    assert next_effective == "Review it"


def test_steer_while_paused_applies_on_resume(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.pause_after_call()
    supervisor.after_agent("implementer", "round 1")

    result: list[str] = []
    waiter = threading.Thread(
        target=lambda: result.append(supervisor.before_agent("judge", "round 1", "Review"))
    )
    waiter.start()
    time.sleep(0.02)
    assert waiter.is_alive()

    supervisor.steer("check for reward hacking")
    supervisor.resume()
    waiter.join(timeout=1)

    assert len(result) == 1
    assert "Review" in result[0]
    assert "check for reward hacking" in result[0]


def test_service_control_commands_ack(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    service = SupervisionService(supervisor)

    pause = service.execute(PauseCommand())
    resume = service.execute(ResumeCommand())
    steer = service.execute(SteerCommand(text="prioritize latency"))

    assert (pause.ack.action, pause.ack.status) == ("pause", "pending")  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297
    assert (resume.ack.action, resume.ack.status) == ("resume", "consumed")  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297
    assert (steer.ack.action, steer.ack.status) == ("steer", "pending")  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297
    # The queued steer reaches the next agent invocation.
    effective = supervisor.before_agent("implementer", "round 1", "Work")
    assert "prioritize latency" in effective


def test_invocation_audit_contains_prompts_and_result(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.before_agent("implementer", "round 4", "Do work", "System rules")
    supervisor.after_agent("implementer", "round 4", result={"summary": "done"})

    events = _events(tmp_path / "run-events.jsonl")
    started = next(e for e in events if e["type"] == "invocation_started")
    finished = next(e for e in events if e["type"] == "invocation_finished")
    assert started["data"]["system_prompt"] == "System rules"
    assert started["data"]["user_prompt"] == "Do work"
    assert finished["invocation_id"] == started["invocation_id"]
    assert finished["data"]["result"] == {"summary": "done"}


def test_inspector_answers_round_and_failure_queries(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    store, run_id = _project_run(tmp_path / "project")
    store.state.save_round(run_id, _round(1, metric=1200.0, passed=True))
    store.state.save_round(
        run_id,
        _round(2, metric=1100.0, passed=False, reason="Judge FAIL: latency regressed"),
    )
    supervisor = RunSupervisor()
    supervisor.attach(store.state.log_directory(run_id), project=store, run_id=run_id)
    inspector = RunInspector(supervisor)
    assert '"round": 2' in inspector.round_detail(2)
    assert "latency regressed" in inspector.answer("why did the judge fail?")


def test_general_chat_is_distinct_from_status_query(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.chat("hello there")
    event_types = [event["type"] for event in _events(tmp_path / "run-events.jsonl")]
    assert event_types == ["server_started", "chat"]


def test_side_channel_chat_output_is_tagged_without_changing_active_agent(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.before_agent("implementer", "round-1", "work")

    with supervisor.presentation_scope(
        agent_kind="chat", round_label="experiment-chat", invocation_id="chat-1"
    ):
        supervisor.publish_agent_output("private chat output")
    supervisor.publish_agent_output("experiment output")

    agent_events = [
        event for event in supervisor.read_events() if event.type is EventType.AGENT_OUTPUT_CHUNK
    ]
    assert [event.agent_kind for event in agent_events] == ["chat", "implementer"]
    assert agent_events[0].round_label == "experiment-chat"
    assert agent_events[0].invocation_id == "chat-1"
    assert agent_events[1].data.content == "experiment output"  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297


def test_bootstrap_events_migrate_to_run_audit_without_replacing_history(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    store, run_id = _project_run(tmp_path / "project")
    logs = store.state.log_directory(run_id)
    historical = RunSupervisor()
    historical.attach(logs, project=store, run_id=run_id)
    historical.record(EventType.RUN_FINISHED, "previous invocation", status="completed")

    bootstrap = tmp_path / "session"
    supervisor = RunSupervisor()
    supervisor.attach(bootstrap)
    supervisor.record(EventType.SERVER_READY, status="active")
    supervisor.attach(logs, project=store, run_id=run_id)
    supervisor.record(EventType.RUN_STARTED, status="active")

    audited = _events(logs / "run-events.jsonl")
    assert [event["type"] for event in audited] == [
        "server_started",
        "run_finished",
        "server_started",
        "server_ready",
        "run_started",
    ]
    assert [event.type for event in supervisor.read_events()] == [
        "server_started",
        "server_ready",
        "run_started",
    ]
    assert [event.type for event in supervisor.read_history_events()] == [
        "server_started",
        "run_finished",
        "server_started",
        "server_ready",
        "run_started",
    ]
    assert supervisor.project_run is not None
    assert supervisor.project_run.project is store
    assert supervisor.project_run.run_id == run_id
    assert supervisor.snapshot().run_id == run_id
    assert supervisor.read_events()[-1].run_id == run_id


def test_history_query_reads_prior_and_current_session_events(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    store, run_id = _project_run(tmp_path / "project")
    logs = store.state.log_directory(run_id)
    historical = RunSupervisor()
    historical.attach(logs, project=store, run_id=run_id)
    historical.record(EventType.ROUND_FINISHED, status="completed", round_label="round-1")

    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "session")
    supervisor.attach(logs, project=store, run_id=run_id)
    supervisor.record(EventType.ROUND_FINISHED, status="completed", round_label="round-2")

    response = SupervisionService(supervisor).execute(HistoryQuery())

    assert [event.round_label for event in response.events if event.round_label] == [
        "round-1",
        "round-2",
    ]
    assert {event.round_label for event in supervisor.read_events()} == {None, "round-2"}


def test_performance_query_reads_canonical_completed_rounds(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    store, run_id = _project_run(tmp_path / "project")
    store.state.save_round(run_id, _round(1, metric=1200.0, passed=True))
    store.state.save_round(run_id, _round(2, metric=None, passed=False))
    store.state.save_round(run_id, _round(3, metric=2400.0, passed=True))
    supervisor = RunSupervisor()
    supervisor.attach(store.state.log_directory(run_id))
    assert SupervisionService(supervisor).execute(PerformanceQuery()).performance == []

    supervisor.attach(store.state.log_directory(run_id), project=store, run_id=run_id)

    response = SupervisionService(supervisor).execute(PerformanceQuery())

    assert [round.round for round in response.performance] == [1, 3]  # noqa: A001  # tracked: #288
    assert response.performance[1].perf_metric == 2400.0
    assert response.performance[1].perf_unit == "total_ops_per_sec"


def test_bootstrap_performance_query_has_no_project_state(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    response = SupervisionService(supervisor).execute(PerformanceQuery())

    assert response.performance == []


@pytest.mark.parametrize(
    "case",
    [
        (
            "plain",
            "perf/metrics.json",
            '{"iteration": 2, "throughput_trend": "improved"}',
            "what is the latest performance result?",
            "throughput_trend",
        ),
        (
            "evolve",
            "population.json",
            '{"generation": 3, "feedback": "judge FAIL: latency regressed"}',
            "why did the judge fail?",
            "latency regressed",
        ),
    ],
)
def test_inspector_searches_portable_loop_state(
    tmp_path: Path, case: tuple[str, str, str, str, str]
) -> None:
    namespace, relative_path, contents, question, expected = case
    store, run_id = _project_run(tmp_path / "project")
    relative = Path(relative_path)
    parent = None if relative.parent == Path() else relative.parent.as_posix()
    state_path = (
        store.state.portable_namespace(run_id, namespace).external_directory(parent) / relative.name
    )
    state_path.write_text(contents, encoding="utf-8")
    supervisor = RunSupervisor()
    supervisor.attach(store.state.log_directory(run_id), project=store, run_id=run_id)

    answer = RunInspector(supervisor).answer(question)

    assert expected in answer


def test_inspector_searches_canonical_local_run_log(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    store, run_id = _project_run(tmp_path / "project")
    (store.state.log_directory(run_id) / "run-20260812-120000.log").write_text(
        "Benchmark throughput reached 2400 ops/s.", encoding="utf-8"
    )
    supervisor = RunSupervisor()
    supervisor.attach(store.state.log_directory(run_id), project=store, run_id=run_id)

    answer = RunInspector(supervisor).answer("what is the latest benchmark result?")

    assert "2400 ops/s" in answer


def test_chat_reports_structured_failed_invocation(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.before_agent("implementer", "round 5", "prompt")
    supervisor.after_agent("implementer", "round 5", error=RuntimeError("agent process exited"))
    answer = supervisor.chat("why did the agent fail?")
    assert "Latest failed agent invocation" in answer
    assert "agent process exited" in answer


def test_failure_events_share_and_promote_a_human_facing_diagnostic(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    error = RuntimeError("token=super-secret agent process exited")
    supervisor.before_agent("implementer", "round 5", "prompt")
    supervisor.after_agent("implementer", "round 5", error=error)
    supervisor.finish(error)

    invocation, phase, terminal = [
        event
        for event in supervisor.read_events()
        if event.type
        in {
            EventType.INVOCATION_FINISHED,
            EventType.PHASE_FINISHED,
            EventType.RUN_FAILED,
        }
    ]
    assert invocation.diagnostic is not None
    assert phase.diagnostic == invocation.diagnostic
    assert terminal.diagnostic is not None
    assert terminal.diagnostic.id == invocation.diagnostic.id
    assert terminal.diagnostic.scope is DiagnosticScope.INVOCATION
    assert terminal.diagnostic.summary == invocation.diagnostic.summary
    assert terminal.diagnostic.detail == invocation.diagnostic.detail
    assert invocation.diagnostic.severity is DiagnosticSeverity.ERROR
    assert terminal.diagnostic.severity is DiagnosticSeverity.FATAL
    assert terminal.diagnostic.retryability is DiagnosticRetryability.UNKNOWN
    assert invocation.data.error == "Agent invocation failed"  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297
    assert terminal.text == "Agent invocation failed"
    assert terminal.diagnostic.detail == "RuntimeError: token=[REDACTED] agent process exited"
    assert "RuntimeError(" not in terminal.text


def test_generic_run_failure_is_fatal_with_unknown_retryability(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.finish(RuntimeError("worker failed"))

    terminal = next(
        event for event in supervisor.read_events() if event.type is EventType.RUN_FAILED
    )
    assert terminal.diagnostic is not None
    assert terminal.diagnostic.scope is DiagnosticScope.RUN
    assert terminal.diagnostic.severity is DiagnosticSeverity.FATAL
    assert terminal.diagnostic.retryability is DiagnosticRetryability.UNKNOWN
    assert sum(event.type is EventType.RUN_FAILED for event in supervisor.read_events()) == 1


@pytest.mark.parametrize(
    "event_type",
    [
        EventType.CONFIGURATION_FAILED,
        EventType.INVOCATION_FINISHED,
        EventType.PHASE_FINISHED,
        EventType.RUN_FAILED,
        EventType.RUN_INTERRUPTED,
    ],
)
def test_operational_failure_events_require_diagnostics(tmp_path, event_type):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    for status in (EventStatus.FAILED, "failed"):
        with pytest.raises(ValueError, match="must include a diagnostic"):
            supervisor.record(event_type, status=status)


def test_semantic_failure_events_remain_valid_without_diagnostics(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    judge = supervisor.record(
        EventType.JUDGE_RESULT,
        status=EventStatus.FAILED,
        data=JudgeResultData(verdict="fail", feedback="incorrect", attempt=1),
    )
    round_finished = supervisor.record(
        EventType.ROUND_FINISHED,
        status=EventStatus.FAILED,
        data=RoundFinishedData(attempts=1, judge_verdict="fail"),
    )

    assert judge is not None
    assert judge.diagnostic is None
    assert round_finished is not None
    assert round_finished.diagnostic is None


def test_capture_failure_emits_nothing_on_success(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    before = supervisor.read_events()

    with supervisor.capture_failure(
        event_type=EventType.PHASE_FINISHED,
        scope=DiagnosticScope.PHASE,
        operation="Background maintenance",
    ):
        pass

    assert supervisor.read_events() == before
    assert supervisor.snapshot().status == "running"


def test_failure_helpers_reject_non_operational_and_terminal_events(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    before = supervisor.read_events()

    with pytest.raises(ValueError, match="without owning run termination"):
        supervisor.record_failure(
            EventType.JUDGE_RESULT,
            RuntimeError("incorrect result"),
            scope=DiagnosticScope.PHASE,
            operation="Judge",
        )
    error = RuntimeError("worker failed")
    for event_type in (EventType.CONFIGURATION_FAILED, EventType.RUN_FAILED):
        with (
            pytest.raises(ValueError, match="without owning run termination"),
            supervisor.capture_failure(
                event_type=event_type,
                scope=DiagnosticScope.RUN,
                operation="Background maintenance",
            ),
        ):
            raise error

    assert supervisor.read_events() == before


def test_finish_is_idempotent(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    supervisor.finish(RuntimeError("first failure"))
    supervisor.finish(RuntimeError("second failure"))

    terminal = [event for event in supervisor.read_events() if event.type is EventType.RUN_FAILED]
    assert len(terminal) == 1
    assert terminal[0].diagnostic is not None
    assert terminal[0].diagnostic.detail == "RuntimeError: first failure"


def test_capture_failure_records_once_and_reraises_without_finishing(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    error = KeyboardInterrupt("background worker stopped")
    before = supervisor.read_events()

    with (
        pytest.raises(KeyboardInterrupt) as raised,
        supervisor.capture_failure(
            event_type=EventType.PHASE_FINISHED,
            scope=DiagnosticScope.PHASE,
            operation="Background maintenance",
            data=PhaseData(phase="maintenance", attempt=1),
            agent_kind="maintenance",
            round_label="round 1",
        ),
    ):
        raise error

    assert raised.value is error
    events = supervisor.read_events()
    assert len(events) == len(before) + 1
    captured = events[-1]
    assert captured.type is EventType.PHASE_FINISHED
    assert captured.status is EventStatus.FAILED
    assert captured.data == PhaseData(phase="maintenance", attempt=1)
    assert captured.agent_kind == "maintenance"
    assert captured.diagnostic is not None
    assert captured.diagnostic.summary == "Background maintenance failed"
    assert supervisor.snapshot().status == "running"
    assert not any(event.type is EventType.RUN_FAILED for event in events)


def test_terminal_wrapper_reuses_an_invocation_diagnostic(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    cause = RuntimeError("token=super-secret agent process exited")
    supervisor.before_agent("implementer", "round 5", "prompt")
    supervisor.after_agent("implementer", "round 5", error=cause)
    wrapper = RuntimeError("run cleanup failed")
    wrapper.__cause__ = cause
    supervisor.finish(wrapper)

    invocation, terminal = [
        event
        for event in supervisor.read_events()
        if event.type in {EventType.INVOCATION_FINISHED, EventType.RUN_FAILED}
    ]
    assert invocation.diagnostic is not None
    assert terminal.diagnostic is not None
    assert terminal.diagnostic.id == invocation.diagnostic.id
    assert terminal.diagnostic.detail == (
        "RuntimeError: run cleanup failed <- RuntimeError: token=[REDACTED] agent process exited"
    )


def test_service_accepts_chat(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    (tmp_path / "logs").mkdir()
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "logs")
    service = SupervisionService(supervisor)
    chat = service.execute(ChatQuery(text="what is the current status?"))
    assert chat.chat.question == "what is the current status?"  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297
    events = _events(tmp_path / "logs" / "run-events.jsonl")
    assert any(event["type"] == "chat" for event in events)
    assert any(event["type"] == "status_query" for event in events)


def test_service_routes_chat_to_configured_agent_handler(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    questions = []
    supervisor.set_chat_handler(lambda question: questions.append(question) or "agent answer")

    response = SupervisionService(supervisor).execute(ChatQuery(text="what changed?"))

    assert response.chat.answer == "agent answer"  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297
    assert questions == ["what changed?"]
    assert response.events[-1].type is EventType.CHAT
    assert response.events[-1].agent_kind == "chat"
    assert _events(tmp_path / "run-events.jsonl")[-1]["type"] == "chat"


def test_chat_without_an_agent_says_so_instead_of_answering_as_if_normal(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """The handler exists only while a run context does.

    Asked during setup or after teardown there is no agent to reach, and the
    keyword matcher must not be presented as the answer.
    """
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    assert supervisor.chat_agent_available() is False

    answer = supervisor.chat("what happened in this experiment?")

    assert "chat agent is not available" in answer
    assert "not finished starting up" in answer
    # It must not send the operator to a command the chat cannot run.
    assert "/history" not in answer


def test_chat_without_an_agent_names_a_finished_run_as_the_reason(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.finish()

    answer = supervisor.chat("what happened?")

    assert "the run has finished" in answer


def test_installing_an_agent_handler_takes_over_from_the_fallback(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    supervisor.set_chat_handler(lambda question: f"agent answered: {question}")
    assert supervisor.chat_agent_available() is True
    assert supervisor.chat("why did round 3 fail?") == "agent answered: why did round 3 fail?"

    # Teardown removes it again, and the fallback says so rather than pretending.
    supervisor.set_chat_handler(None)
    assert supervisor.chat_agent_available() is False
    assert "chat agent is not available" in supervisor.chat("and round 4?")


def test_keyword_fallback_does_not_advertise_slash_commands(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    answer = RunInspector(supervisor).answer("tell me about the experiment")

    assert "/history" not in answer
    assert "keywords" in answer


def test_chat_explains_configuration_failure_without_a_run_context(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    error = RuntimeError("agent.toml was not found")
    supervisor.record(
        EventType.CONFIGURATION_FAILED,
        status="failed",
        data=ConfigurationFailedData(
            code="config_load_failed",
            stage="config_loading",
            message="agent.toml was not found",
            usage=None,
            exit_code=2,
        ),
        diagnostic=exception_to_diagnostic(
            error,
            scope=DiagnosticScope.CONFIGURATION,
            operation="Configuration",
        ),
    )

    response = SupervisionService(supervisor).execute(ChatQuery(text="why did startup fail?"))

    assert "config_loading" in response.chat.answer  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297
    assert "agent.toml was not found" in response.chat.answer  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297


def test_run_context_chat_exposes_trajectory_without_inlining_it_in_prompt(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    store, run_id = _project_run(tmp_path / "project")
    portable_metrics = (
        store.state.portable_namespace(run_id, "plain").external_directory("perf") / "metrics.json"
    )
    portable_metrics.write_text(
        '{"iteration": 2, "throughput_trend": "improved"}', encoding="utf-8"
    )
    run_log = store.state.log_directory(run_id) / "run-20260812-120000.log"
    run_log.write_text("Round 2 improved throughput.", encoding="utf-8")
    supervisor = RunSupervisor()
    supervisor.attach(store.state.log_directory(run_id), project=store, run_id=run_id)
    ctx = _RunContext.__new__(_RunContext)
    ctx.supervisor = supervisor
    ctx.agent_client = Mock()
    ctx.agent_client.invoke_text.return_value = "It improved in round 2."
    ctx._paths = RunPaths(  # noqa: SLF001  # tracked: #288
        project_root=store.root,
        log_dir=store.state.log_directory(run_id),
        run_log_path=run_log,
    )
    ctx.project = store
    ctx.run_id = run_id
    ctx.gpu_env = dict
    ctx._progress_stack = []  # noqa: SLF001  # tracked: #288
    ctx._chat_lock = threading.Lock()  # noqa: SLF001  # tracked: #288
    ctx._chat_history = []  # noqa: SLF001  # tracked: #288
    ctx.logger = Mock()
    ctx.logger.file = Mock()

    answer = ctx.chat("what improved?")

    assert answer == "It improved in round 2."
    invocation = ctx.agent_client.invoke_text.call_args.kwargs
    assert invocation["kind"] == "chat"
    assert "response_cls" not in invocation
    assert invocation["user_prompt"] == "what improved?"
    assert "Round 2 improved throughput." not in invocation["user_prompt"]
    assert "_vibesys_chat/trajectory/" in invocation["system_prompt"]
    assert "read-only investigation agent" in invocation["system_prompt"]
    trajectory = store.root / "_vibesys_chat" / "trajectory"
    assert json.loads((trajectory / "state/plain/perf/metrics.json").read_text()) == {
        "iteration": 2,
        "throughput_trend": "improved",
    }
    assert (trajectory / "logs" / run_log.name).read_text() == "Round 2 improved throughput."
    transcript = store.root / "_vibesys_chat" / "conversation.jsonl"
    assert json.loads(transcript.read_text()) == {
        "question": "what improved?",
        "answer": "It improved in round 2.",
    }

    ctx.agent_client.invoke_text.side_effect = RuntimeError("agent unavailable")
    with pytest.raises(RuntimeError, match="Chat agent failed: RuntimeError: agent unavailable"):
        ctx.chat("what is the current status?")
    continuation = ctx.agent_client.invoke_text.call_args.kwargs
    assert continuation["user_prompt"] == "what is the current status?"
    assert "It improved in round 2." not in continuation["user_prompt"]
    assert "_vibesys_chat/instructions.md" in continuation["system_prompt"]
    assert "Prefer targeted commands" not in continuation["system_prompt"]
    assert ctx._load_chat_history() == [("what improved?", "It improved in round 2.")]  # noqa: SLF001  # tracked: #288


def test_socket_transport_supports_multiple_clients_and_event_replay(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "logs")
    service = SupervisionService(supervisor)
    socket_path = Path("/tmp") / f"vibesys-test-{uuid.uuid4().hex}.sock"  # noqa: S108  # tracked: #288

    with SupervisionSocketServer(socket_path, service):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as first:
            first.connect(str(socket_path))
            first_file = first.makefile("rwb")
            first_file.write(SnapshotQuery().model_dump_json().encode() + b"\n")
            first_file.flush()
            status = json.loads(first_file.readline())
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as second:
            second.connect(str(socket_path))
            second_file = second.makefile("rwb")
            second_file.write(EventsQuery(after_sequence=0).model_dump_json().encode() + b"\n")
            second_file.flush()
            replay = json.loads(second_file.readline())

    assert status["ok"] is True
    assert status["snapshot"]["status"] == "running"
    sequences = [event["sequence"] for event in replay["events"]]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert any(event["type"] == "server_started" for event in replay["events"])


def test_socket_transport_returns_clear_chat_agent_errors(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "logs")

    def fail_chat(question: str) -> str:
        raise RuntimeError(  # noqa: TRY003  # tracked: #288
            f"token=super-secret Chat agent failed while answering: {question}"
        )

    supervisor.set_chat_handler(fail_chat)
    socket_path = Path("/tmp") / f"vibesys-test-{uuid.uuid4().hex}.sock"  # noqa: S108  # tracked: #288

    with SupervisionSocketServer(socket_path, SupervisionService(supervisor)):  # noqa: SIM117  # tracked: #288
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            stream = client.makefile("rwb")
            stream.write(ChatQuery(text="what happened?").model_dump_json().encode() + b"\n")
            stream.flush()
            response = json.loads(stream.readline())

    assert response["ok"] is False
    assert response["error"] == "Request failed"
    assert "super-secret" not in response["error"]
    assert response["diagnostic"]["scope"] == "request"
    assert response["diagnostic"]["summary"] == "Request failed"
    assert response["diagnostic"]["detail"] == (
        "RuntimeError: token=[REDACTED] Chat agent failed while answering: what happened?"
    )


def test_socket_subscription_replays_then_streams_new_events(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "logs")
    service = SupervisionService(supervisor)
    socket_path = Path("/tmp") / f"vibesys-test-{uuid.uuid4().hex}.sock"  # noqa: S108  # tracked: #288

    with SupervisionSocketServer(socket_path, service):  # noqa: SIM117  # tracked: #288
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(socket_path))
            stream = client.makefile("rwb")
            request = SubscribeRequest(after_sequence=0)
            stream.write(request.model_dump_json().encode() + b"\n")
            stream.flush()
            subscribed = json.loads(stream.readline())
            replay = json.loads(stream.readline())
            supervisor.record(EventType.CHAT, "hello", status="answered")
            streamed = json.loads(stream.readline())

    assert subscribed["type"] == "subscribed"
    assert replay["type"] == "event_batch"
    assert streamed["type"] == "event"
    assert streamed["event"]["type"] == "chat"


def test_socket_subscription_reports_structured_stream_failures(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path / "logs")
    service = SupervisionService(supervisor)
    socket_path = Path("/tmp") / f"vibesys-test-{uuid.uuid4().hex}.sock"  # noqa: S108  # tracked: #288

    def fail_replay(after_sequence: int):  # noqa: ANN202  # tracked: #288
        del after_sequence
        raise RuntimeError("event store is unavailable")  # noqa: TRY003  # tracked: #288

    monkeypatch.setattr(service, "events", fail_replay)
    with SupervisionSocketServer(socket_path, service):  # noqa: SIM117  # tracked: #288
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(socket_path))
            stream = client.makefile("rwb")
            stream.write(SubscribeRequest(after_sequence=0).model_dump_json().encode() + b"\n")
            stream.flush()
            subscribed = json.loads(stream.readline())
            failure = json.loads(stream.readline())

    assert subscribed["type"] == "subscribed"
    assert failure == {
        "type": "protocol_error",
        "request_id": subscribed["request_id"],
        "code": "stream_failed",
        "message": "Event stream failed",
        "diagnostic": {
            "id": failure["diagnostic"]["id"],
            "code": "stream_failed",
            "summary": "Event stream failed",
            "detail": "RuntimeError: event store is unavailable",
            "hint": None,
            "scope": "protocol",
            "severity": "error",
            "retryability": "unknown",
            "cause_id": None,
            "debug_ref": None,
        },
    }


def test_cli_parse_failure_is_streamed_after_client_attaches():  # noqa: ANN201  # tracked: #288
    session_dir = Path("/tmp") / f"vs-test-{uuid.uuid4().hex}"  # noqa: S108  # tracked: #288
    session_dir.mkdir()
    socket_path = session_dir / "control.sock"
    process = subprocess.Popen(  # noqa: S603  # tracked: #288
        [
            sys.executable,
            "-m",
            "vibesys",
            "--headless",
            "--control-socket",
            str(socket_path),
            "--not-a-real-option",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )
    try:
        deadline = time.monotonic() + 5
        while not socket_path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if not socket_path.exists():
            output, error = process.communicate(timeout=5)
            pytest.fail(
                f"backend did not create control socket: stdout={output!r} stderr={error!r}"
            )

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(socket_path))
            stream = client.makefile("rwb")
            stream.write(SubscribeRequest(after_sequence=0).model_dump_json().encode() + b"\n")
            stream.flush()
            messages = []
            events = []
            while True:
                line = stream.readline()
                if not line:
                    break
                messages.append(json.loads(line))
                events = []
                for message in messages:
                    if message["type"] == "event":
                        events.append(message["event"])
                    elif message["type"] == "event_batch":
                        events.extend(message["events"])
                if any(event["type"] == "configuration_failed" for event in events):
                    break
            stream.close()

        assert process.wait(timeout=5) == 2
        audited_events = _events(session_dir / "run-events.jsonl")
    finally:
        if process.poll() is None:
            process.kill()
        stdout, stderr = process.communicate(timeout=5)
        shutil.rmtree(session_dir, ignore_errors=True)

    failures = [event for event in events if event["type"] == "configuration_failed"]
    assert failures[0]["data"]["code"] == "invalid_arguments"
    assert "--not-a-real-option" in failures[0]["data"]["message"]
    assert [event["type"] for event in audited_events].count("configuration_failed") == 1
    assert not any(event["type"] == "run_failed" for event in audited_events)
    assert stdout == ""
    assert stderr == ""


def test_supervision_runtime_streams_configuration_failure_before_exiting():  # noqa: ANN201, PLR0915  # tracked: #288
    session_dir = Path("/tmp") / f"vs-runtime-test-{uuid.uuid4().hex}"  # noqa: S108  # tracked: #288
    socket_path = session_dir / "control.sock"
    received_events = []
    chat_responses = []

    def subscribe_until_failure() -> None:
        deadline = time.monotonic() + 5
        while not socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(socket_path))
            stream = client.makefile("rwb")
            stream.write(SubscribeRequest(after_sequence=0).model_dump_json().encode() + b"\n")
            stream.flush()
            while True:
                message = json.loads(stream.readline())
                if message["type"] == "event":
                    received_events.append(message["event"])
                elif message["type"] == "event_batch":
                    received_events.extend(message["events"])
                if any(event["type"] == "configuration_failed" for event in received_events):
                    break
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as chat_client:
                chat_client.settimeout(5)
                chat_client.connect(str(socket_path))
                chat_stream = chat_client.makefile("rwb")
                chat_stream.write(
                    ChatQuery(text="why did startup fail?").model_dump_json().encode() + b"\n"
                )
                chat_stream.flush()
                chat_responses.append(json.loads(chat_stream.readline()))
                chat_stream.close()
            stream.close()

    subscriber = threading.Thread(target=subscribe_until_failure)
    subscriber.start()
    failure = ConfigurationError(
        ConfigurationDiagnostic(
            code="invalid_arguments",
            stage="argument_parsing",
            message="unknown token=super-secret option --bad",
            usage="usage: vibesys --token=super-secret",
        )
    )
    try:
        with pytest.raises(ConfigurationError) as raised:
            run_server(lambda: (_ for _ in ()).throw(failure), socket_path=socket_path)
        assert raised.value is failure
        subscriber.join(timeout=5)
        assert not subscriber.is_alive()
        configuration_event = next(
            event for event in received_events if event["type"] == "configuration_failed"
        )
        assert configuration_event["data"] == {
            "kind": "configuration_failed",
            "code": "invalid_arguments",
            "stage": "argument_parsing",
            "message": "unknown token=[REDACTED] option --bad",
            "usage": "usage: vibesys --token=[REDACTED]",
            "exit_code": 2,
        }
        assert configuration_event["text"] == "unknown token=[REDACTED] option --bad"
        assert (
            configuration_event["diagnostic"]["summary"] == "unknown token=[REDACTED] option --bad"
        )
        assert configuration_event["diagnostic"]["detail"] == (
            "Stage: argument_parsing\nExit code: 2"
        )
        assert configuration_event["diagnostic"]["hint"] == "usage: vibesys --token=[REDACTED]"
        assert not any(event["type"] == "run_failed" for event in received_events)
        assert chat_responses[0]["ok"] is True
        assert "unknown token=[REDACTED] option --bad" in chat_responses[0]["chat"]["answer"]
    finally:
        subscriber.join(timeout=5)
        shutil.rmtree(session_dir, ignore_errors=True)


def test_run_context_records_invocation_boundary(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    ctx = _RunContext.__new__(_RunContext)
    ctx.supervisor = supervisor
    ctx.agent_client = Mock()
    ctx.agent_client.invoke.return_value = {"summary": "measured"}
    ctx._paths = RunPaths(  # noqa: SLF001  # tracked: #288
        project_root=tmp_path,
        log_dir=tmp_path / "logs",
        run_log_path=tmp_path / "run.log",
    )
    ctx.gpu_env = dict
    ctx._progress_stack = []  # noqa: SLF001  # tracked: #288

    result = ctx.invoke(
        kind="implementer",
        system_prompt="system",
        user_prompt="original",
        response_cls=dict,  # pyright: ignore[reportArgumentType]  # tracked: #297
        fallback_factory=dict,  # pyright: ignore[reportArgumentType]  # tracked: #297
        round_label="round 6 attempt 2",
    )

    assert result == {"summary": "measured"}
    sent_prompt = ctx.agent_client.invoke.call_args.kwargs["user_prompt"]
    assert sent_prompt == "original"
    events = _events(tmp_path / "run-events.jsonl")
    started = next(event for event in events if event["type"] == "invocation_started")
    assert started["data"]["user_prompt"] == "original"


def test_committed_protocol_schema_matches_python_contract():  # noqa: ANN201  # tracked: #288
    schema_path = Path("clients/tui/src/generated/protocol.schema.json")
    assert json.loads(schema_path.read_text()) == ProtocolDocument.model_json_schema()
