"""Server projection tests for the authoritative agent-run aggregate."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypedDict, Unpack

from tests.server.support import build_server_parts

from server.api.experiments import build_experiment_log
from server.api.protocol import ExperimentQuery, PerformanceQuery
from vibesys.loops.agent.model import (
    AgentRunState,
    Hypothesis,
    HypothesisMeasurement,
    HypothesisResolution,
    HypothesisReview,
    HypothesisStrategy,
)
from vibesys.loops.agent.state import AgentRunStateStore
from vibesys.schemas import HypothesisOutcome, OrchestratorPlan
from vs_loop_state import MetricComparison, RoundRecord
from vs_project import AgentRunConfiguration, Project, RunEnvironmentRecord

if TYPE_CHECKING:
    from pathlib import Path


class _RoundFields(TypedDict, total=False):
    """Keyword fields of ``RoundRecord``, so helper overrides stay checked."""

    round_number: int
    commit: str | None
    perf_metric: float | None
    perf_unit: str | None
    passed: bool
    profile_skipped: bool
    reviewed: bool
    hypothesis_id: str | None
    hypothesis_declared_outcome: str | None
    judge_verdict: Literal["pass", "fail", "deferred"] | None
    hypothesis_outcome: str | None
    hypothesis_claim: str | None
    hypothesis_task: str | None
    hypothesis_parent_round: int | None
    hypothesis_parent_commit: str | None
    metrics: dict[str, float]
    evaluation_artifact: str | None
    official_evaluation: bool
    official_evaluation_reason: str | None
    candidate_disposition: str
    candidate_metrics: dict[str, float]
    candidate_evaluation_artifact: str | None
    candidate_operating_point: str
    candidate_retention_reason: str
    candidate_retained: bool | None
    perf_direction: Literal["max", "min"] | None
    perf_baseline_round: int | None
    perf_baseline_commit: str | None
    perf_baseline_metric: float | None
    perf_delta_pct: float | None
    perf_comparison: MetricComparison | None


class _HypothesisFields(TypedDict, total=False):
    """Keyword fields of ``Hypothesis``, so helper overrides stay checked."""

    hypothesis_id: str
    plan: OrchestratorPlan
    started_round: int
    parent_round: int | None
    parent_commit: str | None
    rounds: list[RoundRecord]
    feedback: str | None
    next_step: str | None
    continuation_rounds: int
    revert_applied: bool
    revert_commit: str | None
    gate_revalidation_pending: bool
    gate_approved_perf_metric: float | None
    gate_approved_perf_unit: str | None
    gate_approved_metrics: dict[str, float]
    gate_approved_evaluation_artifact: str | None
    gate_approved_candidate_disposition: str
    gate_approved_candidate_metrics: dict[str, float]
    gate_approved_candidate_evaluation_artifact: str | None
    gate_approved_candidate_operating_point: str
    gate_approved_candidate_retention_reason: str
    gate_candidate_commit: str | None
    gate_accuracy_passed: bool
    declared_outcome: HypothesisOutcome | None
    review: HypothesisReview
    resolution: HypothesisResolution | None
    measurement: HypothesisMeasurement | None
    candidate_retained: bool | None
    strategy: HypothesisStrategy
    strategy_reason: str | None


def _round(number: int, **overrides: Unpack[_RoundFields]) -> RoundRecord:
    fields: _RoundFields = {
        "round_number": number,
        "commit": f"c{number}",
        "perf_metric": None,
        "perf_unit": None,
        "passed": False,
    }
    fields.update(overrides)
    return RoundRecord(**fields)


def _hypothesis(
    identifier: str, started_round: int, /, **overrides: Unpack[_HypothesisFields]
) -> Hypothesis:
    fields: _HypothesisFields = {
        "hypothesis_id": identifier,
        "plan": OrchestratorPlan(
            hypothesis_id=identifier,
            hypothesis=f"claim for {identifier}",
            task=f"test {identifier}",
            pass_criteria="",
            reasoning="",
        ),
        "started_round": started_round,
    }
    fields.update(overrides)
    return Hypothesis(**fields)


def test_projection_uses_nested_rounds_and_one_official_measurement_tuple() -> None:
    hypothesis = _hypothesis(
        "H-01",
        1,
        rounds=[
            _round(1, hypothesis_id="H-01", perf_metric=100.0, perf_unit="ops_s"),
            _round(2, hypothesis_id="H-01", perf_metric=125.0, perf_unit="ops_s"),
        ],
        review=HypothesisReview.PASS,
        resolution=HypothesisResolution.DISPROVEN,
        measurement=HypothesisMeasurement(
            round=1,
            metric="throughput",
            value=100.0,
            unit="ops_s",
            direction="max",
            baseline_value=110.0,
            delta_pct=-9.09,
        ),
        candidate_retained=False,
        strategy=HypothesisStrategy.ABANDONED,
        strategy_reason="The official baseline regressed.",
    )

    (entry,) = build_experiment_log(AgentRunState(hypotheses=[hypothesis]))

    assert [round_.round for round_ in entry.rounds] == [1, 2]
    assert (entry.first_round, entry.last_round) == (1, 2)
    assert entry.resolved_outcome == "disproven"
    assert entry.judge_verdict == "pass"
    assert entry.kept is False
    assert entry.strategy_disposition == "abandoned"
    # Do not combine the newer second-round value with the official first-round
    # causal comparison.
    assert (entry.perf_metric, entry.perf_unit, entry.perf_delta_pct) == (
        100.0,
        "ops_s",
        -9.09,
    )
    assert (entry.perf_metric_name, entry.perf_direction, entry.perf_baseline_value) == (
        "throughput",
        "max",
        110.0,
    )


def test_projection_surfaces_active_hypothesis_before_a_round_finishes() -> None:
    state = AgentRunState(
        active_hypothesis_id="H-02",
        hypotheses=[_hypothesis("H-02", 2)],
    )

    (entry,) = build_experiment_log(state)

    assert entry.active is True
    assert entry.rounds == []
    assert (entry.first_round, entry.last_round) == (2, 2)
    assert entry.claim == "claim for H-02"


def test_projection_uses_the_orchestrator_title_when_present() -> None:
    hypothesis = _hypothesis(
        "H-01",
        1,
        plan=OrchestratorPlan(
            hypothesis_id="H-01",
            title="Batch decode requests",
            hypothesis="claim for H-01",
            task="test H-01",
            pass_criteria="",
            reasoning="",
        ),
    )

    (entry,) = build_experiment_log(AgentRunState(hypotheses=[hypothesis]))

    assert entry.title == "Batch decode requests"


def test_projection_derives_a_title_from_the_claim_when_the_plan_title_is_empty() -> None:
    hypothesis = _hypothesis(
        "H-01",
        1,
        plan=OrchestratorPlan(
            hypothesis_id="H-01",
            hypothesis="Batching decode requests reduces overhead. More detail follows.",
            task="test H-01",
            pass_criteria="",
            reasoning="",
        ),
    )

    (entry,) = build_experiment_log(AgentRunState(hypotheses=[hypothesis]))

    assert entry.title == "Batching decode requests reduces overhead"


def test_projection_title_is_none_without_any_text() -> None:
    hypothesis = _hypothesis(
        "H-01",
        1,
        plan=OrchestratorPlan(
            hypothesis_id="H-01",
            hypothesis="",
            task="test H-01",
            pass_criteria="",
            reasoning="",
        ),
    )

    (entry,) = build_experiment_log(AgentRunState(hypotheses=[hypothesis]))

    assert entry.title is None


def test_projection_orders_hypotheses_by_started_round() -> None:
    state = AgentRunState(
        hypotheses=[
            _hypothesis("H-B", 3, rounds=[_round(3, hypothesis_id="H-B")]),
            _hypothesis("H-A", 1, rounds=[_round(1, hypothesis_id="H-A")]),
        ]
    )

    entries = build_experiment_log(state)

    assert [entry.hypothesis_id for entry in entries] == ["H-A", "H-B"]


def _configuration() -> AgentRunConfiguration:
    return AgentRunConfiguration(
        outer_loop="agent",
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
        run_environment=RunEnvironmentRecord(name="local"),
    )


def _project_run(
    project: Path,
    configuration: AgentRunConfiguration | None = None,
) -> tuple[Project, str]:
    project.mkdir()
    (project / "OBJECTIVE.md").write_text("Make the queue fast.\n", encoding="utf-8")
    vibesys_project = Project.open(project)
    vibesys_project.state.create_project("queue")
    manifest = vibesys_project.state.new_run_manifest(
        "queue",
        run_id="queue-run",
        branch="vibesys/queue-run",
        vibesys_version="0.2.0-test",
        configuration=configuration or _configuration(),
        trusted_input_baseline="0" * 40,
    )
    vibesys_project.state.create_run(manifest)
    return vibesys_project, manifest.run_id


def test_service_reads_only_authoritative_agent_state(tmp_path: Path) -> None:
    project, run_id = _project_run(tmp_path / "project")
    portable = project.state.portable_namespace(run_id, "agent")
    AgentRunStateStore(portable).save(
        AgentRunState(
            active_hypothesis_id="H-02",
            hypotheses=[
                _hypothesis("H-01", 1, rounds=[_round(1, hypothesis_id="H-01")]),
                _hypothesis("H-02", 2),
            ],
        )
    )
    parts = build_server_parts(project.state.log_directory(run_id), project=project, run_id=run_id)
    response = parts.api.execute(ExperimentQuery())

    assert [entry.hypothesis_id for entry in response.experiments] == ["H-01", "H-02"]
    assert response.experiments[1].active is True
    assert response.experiments_ready is True


def test_service_reads_performance_from_authoritative_agent_state(tmp_path: Path) -> None:
    project, run_id = _project_run(tmp_path / "project")
    portable = project.state.portable_namespace(run_id, "agent")
    AgentRunStateStore(portable).save(
        AgentRunState(
            hypotheses=[
                _hypothesis(
                    "H-01",
                    1,
                    rounds=[
                        _round(
                            1,
                            hypothesis_id="H-01",
                            perf_metric=42.0,
                            perf_unit="ops_s",
                            passed=True,
                        )
                    ],
                )
            ]
        )
    )
    parts = build_server_parts(project.state.log_directory(run_id), project=project, run_id=run_id)
    response = parts.api.execute(PerformanceQuery())

    assert [(item.round, item.perf_metric) for item in response.performance] == [(1, 42.0)]


def test_service_adapts_legacy_state_read_only(tmp_path: Path) -> None:
    project, run_id = _project_run(tmp_path / "project")
    project.state.save_round(
        run_id,
        _round(
            1,
            hypothesis_id="H-01",
            hypothesis_claim="legacy claim",
            hypothesis_task="legacy task",
        ),
    )
    portable = project.state.portable_namespace(run_id, "agent")
    store = AgentRunStateStore(portable)
    parts = build_server_parts(project.state.log_directory(run_id), project=project, run_id=run_id)
    (entry,) = parts.api.execute(ExperimentQuery()).experiments

    assert entry.hypothesis_id == "H-01"
    assert entry.claim == "legacy claim"
    assert store.load_optional() is None


def test_service_rebuilds_legacy_measurement_and_resolution_from_round_evidence(
    tmp_path: Path,
) -> None:
    """Legacy summaries may omit measurements, but nested evidence is complete."""
    configuration = _configuration().model_copy(update={"objectives": ("ops_s:max",)})
    project, run_id = _project_run(tmp_path / "project", configuration)
    project.state.save_round(
        run_id,
        _round(
            1,
            commit="a" * 40,
            hypothesis_id="H-parent",
            hypothesis_outcome="proven",
            passed=True,
            reviewed=True,
            official_evaluation=True,
            perf_metric=100.0,
            perf_unit="ops_s",
        ),
    )
    project.state.save_round(
        run_id,
        _round(
            2,
            commit="b" * 40,
            hypothesis_id="H-regression",
            hypothesis_parent_round=1,
            hypothesis_parent_commit="a" * 40,
            hypothesis_outcome="proven",
            passed=True,
            reviewed=True,
            official_evaluation=True,
            perf_metric=90.0,
            perf_unit="ops_s",
        ),
    )
    parts = build_server_parts(project.state.log_directory(run_id), project=project, run_id=run_id)
    entries = parts.api.execute(ExperimentQuery()).experiments

    regression = next(entry for entry in entries if entry.hypothesis_id == "H-regression")
    assert regression.resolved_outcome == "disproven"
    assert (regression.perf_metric, regression.perf_unit, regression.perf_delta_pct) == (
        90.0,
        "ops_s",
        -10.0,
    )
    # The configured objective direction and the rebuilt causal baseline reach
    # the wire, so the client can label the numbers above.
    assert (
        regression.perf_metric_name,
        regression.perf_direction,
        regression.perf_baseline_value,
    ) == ("ops_s", "max", 100.0)


def test_service_returns_authoritative_empty_log_after_attach(tmp_path: Path) -> None:
    project, run_id = _project_run(tmp_path / "project")
    parts = build_server_parts(project.state.log_directory(run_id), project=project, run_id=run_id)
    response = parts.api.execute(ExperimentQuery())

    assert response.experiments == []
    assert response.experiments_ready is True
