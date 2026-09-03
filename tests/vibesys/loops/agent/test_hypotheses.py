"""Tests for the unified hypothesis aggregate and its pure transitions."""

from typing import Literal

import pytest
from pydantic import ValidationError

from vibesys.loops.agent.hypotheses import (
    ResolutionEvidence,
    adopt_metric_space,
    append_round,
    apply_strategy_updates,
    metric_baseline,
    project_round_evidence,
    reproject_run_evidence,
    resolve_hypothesis_outcome,
    scalar_candidate_retained,
    start_hypothesis,
    update_active_hypothesis,
)
from vibesys.loops.agent.model import (
    AgentRunState,
    Hypothesis,
    HypothesisResolution,
    HypothesisStrategy,
)
from vibesys.loops.metrics import Measurement, MetricComparison, MetricSpace, Objective
from vibesys.schemas import (
    HypothesisOutcome,
    HypothesisStrategyUpdate,
    OrchestratorPlan,
)
from vs_loop_state import RoundRecord


def _plan(identifier: str, *, updates: list[HypothesisStrategyUpdate] | None = None):  # noqa: ANN202
    return OrchestratorPlan(
        hypothesis_id=identifier,
        hypothesis=f"claim {identifier}",
        hypothesis_updates=updates or [],
        task=f"implement {identifier}",
        pass_criteria="tests pass",  # noqa: S106
        reasoning="test the claim",
    )


def _round(  # noqa: PLR0913
    number: int,
    metric: float | None,
    *,
    hypothesis_id: str,
    parent_round: int | None = None,
    parent_commit: str | None = None,
    outcome: str = "proven",
    declared: str | None = "nominated",
    direction: Literal["max", "min"] = "max",
    retained: bool | None = True,
    comparison: MetricComparison | None = None,
) -> RoundRecord:
    return RoundRecord(
        round_number=number,
        commit=f"{number:040x}",
        perf_metric=metric,
        perf_unit="total_ops_per_sec" if metric is not None else None,
        passed=True,
        hypothesis_id=hypothesis_id,
        hypothesis_declared_outcome=declared,
        judge_verdict="pass",
        hypothesis_outcome=outcome,
        hypothesis_claim=f"claim {hypothesis_id}",
        hypothesis_task=f"implement {hypothesis_id}",
        hypothesis_parent_round=parent_round,
        hypothesis_parent_commit=parent_commit,
        metrics={"total_ops_per_sec": metric} if metric is not None else {},
        official_evaluation=metric is not None,
        perf_direction=direction if metric is not None else None,
        candidate_retained=retained,
        perf_comparison=comparison,
    )


def _legacy_evidence_round(
    number: int,
    metric: float,
    *,
    parent_round: int | None = None,
) -> RoundRecord:
    return RoundRecord(
        round_number=number,
        commit=f"{number:040x}",
        perf_metric=metric,
        perf_unit="ops",
        passed=True,
        reviewed=True,
        hypothesis_id="H-1",
        hypothesis_declared_outcome="nominated",
        hypothesis_outcome="proven",
        hypothesis_claim="claim H-1",
        hypothesis_task="implement H-1",
        hypothesis_parent_round=parent_round,
        official_evaluation=True,
    )


def test_hypothesis_owns_all_of_its_rounds_and_active_is_only_a_pointer() -> None:
    initial = AgentRunState()
    started = start_hypothesis(initial, _plan("H-1"), started_round=1)
    continued = append_round(
        started,
        _round(1, None, hypothesis_id="H-1", outcome="continue", declared="continue"),
        keep_active=True,
    )
    completed = append_round(
        continued,
        _round(2, 100.0, hypothesis_id="H-1"),
        keep_active=False,
    )

    hypothesis = completed.by_id("H-1")
    assert hypothesis is not None
    assert [record.round_number for record in hypothesis.rounds] == [1, 2]
    assert completed.rounds == hypothesis.rounds
    assert completed.active_hypothesis_id is None
    assert initial.hypotheses == []
    started_hypothesis = started.by_id("H-1")
    assert started_hypothesis is not None
    assert started_hypothesis.rounds == []


def test_operational_restart_fields_live_on_the_same_hypothesis() -> None:
    state = start_hypothesis(AgentRunState(), _plan("H-1"), started_round=1)
    active = state.active_hypothesis
    assert active is not None
    active = active.clone()
    active.feedback = "measure the producer path"
    active.next_step = "add the producer-local cache"
    active.continuation_rounds = 1
    active.gate_revalidation_pending = True

    updated = update_active_hypothesis(state, active)

    assert updated.active_hypothesis is not None
    assert updated.active_hypothesis.feedback == "measure the producer path"
    assert updated.active_hypothesis.next_step == "add the producer-local cache"
    assert updated.active_hypothesis.continuation_rounds == 1
    assert state.active_hypothesis is not None
    assert state.active_hypothesis.feedback is None


def test_aggregate_accessors_do_not_expose_owned_mutable_hypotheses() -> None:
    state = start_hypothesis(AgentRunState(), _plan("H-1"), started_round=1)

    by_id = state.by_id("H-1")
    active = state.active_hypothesis
    assert by_id is not None
    assert active is not None
    by_id.feedback = "mutated lookup"
    active.feedback = "mutated active"

    assert state.hypotheses[0].feedback is None


def test_plan_strategy_updates_and_start_are_one_pure_transition() -> None:
    first = start_hypothesis(AgentRunState(), _plan("old"), started_round=1)
    completed = append_round(
        first,
        _round(1, 100.0, hypothesis_id="old"),
        keep_active=False,
    )
    update = HypothesisStrategyUpdate(
        hypothesis_id="old",
        disposition="abandoned",
        reason="A better direction supersedes it.",
    )

    started = start_hypothesis(
        completed,
        _plan("new", updates=[update]),
        started_round=2,
        parent_round=1,
        parent_commit="a" * 40,
    )

    old = started.by_id("old")
    assert old is not None
    assert old.strategy is HypothesisStrategy.ABANDONED
    assert started.active_hypothesis_id == "new"
    unchanged_old = completed.by_id("old")
    assert unchanged_old is not None
    assert unchanged_old.strategy is HypothesisStrategy.AVAILABLE


def test_strategy_updates_reject_unknown_active_and_incomplete_hypotheses() -> None:
    active = start_hypothesis(AgentRunState(), _plan("active"), started_round=1)
    abandon_active = HypothesisStrategyUpdate(
        hypothesis_id="active",
        disposition="abandoned",
        reason="stop",
    )
    with pytest.raises(ValueError, match="cannot abandoned active"):
        apply_strategy_updates(active, [abandon_active])

    incomplete = active.clone()
    incomplete.active_hypothesis_id = None
    with pytest.raises(ValueError, match="incomplete hypothesis"):
        apply_strategy_updates(incomplete, [abandon_active])

    unknown = HypothesisStrategyUpdate(
        hypothesis_id="missing",
        disposition="parked",
        reason="no evidence",
    )
    with pytest.raises(ValueError, match="unknown hypothesis"):
        apply_strategy_updates(AgentRunState(), [unknown])


def test_aggregate_rejects_duplicate_ids_rounds_and_dangling_active_pointer() -> None:
    first = Hypothesis(hypothesis_id="H-1", plan=_plan("H-1"), started_round=1)
    with pytest.raises(ValidationError, match="hypothesis IDs must be unique"):
        AgentRunState(hypotheses=[first, first.clone()])

    with pytest.raises(ValidationError, match="active_hypothesis_id"):
        AgentRunState(active_hypothesis_id="missing")

    one = first.clone()
    one.rounds = [_round(1, None, hypothesis_id="H-1")]
    two = Hypothesis(
        hypothesis_id="H-2",
        plan=_plan("H-2"),
        started_round=1,
        rounds=[_round(1, None, hypothesis_id="H-2")],
    )
    with pytest.raises(ValidationError, match="globally unique"):
        AgentRunState(hypotheses=[one, two])


def test_queue_rs_regression_is_disproven_and_not_retained() -> None:
    parent = start_hypothesis(AgentRunState(), _plan("m2"), started_round=2)
    parent = append_round(
        parent,
        _round(2, 104_257_741.0, hypothesis_id="m2"),
        keep_active=False,
    )
    child = start_hypothesis(
        parent,
        _plan("m3"),
        started_round=3,
        parent_round=2,
        parent_commit=f"{2:040x}",
    )
    child = append_round(
        child,
        _round(
            3,
            97_028_091.721612,
            hypothesis_id="m3",
            parent_round=2,
            parent_commit=f"{2:040x}",
            retained=False,
        ),
        keep_active=False,
    )

    m3 = child.by_id("m3")
    assert m3 is not None
    assert m3.resolution is HypothesisResolution.DISPROVEN
    assert m3.candidate_retained is False
    assert m3.measurement is not None
    assert m3.measurement.baseline_round == 2
    assert m3.measurement.delta_pct is not None
    assert m3.measurement.delta_pct < 0


def test_legacy_performance_remains_visible_without_objective_direction() -> None:
    record = _legacy_evidence_round(1, 100.0)
    projected = project_round_evidence(
        Hypothesis(hypothesis_id="H-1", plan=_plan("H-1"), started_round=1),
        record,
        prior_rounds=[],
        space=MetricSpace(),
    )

    assert projected.measurement is not None
    assert projected.measurement.value == 100.0
    assert projected.measurement.metric == "ops"
    assert projected.measurement.direction is None


def test_legacy_resolution_fails_closed_without_objective_direction() -> None:
    record = _legacy_evidence_round(1, 100.0)
    projected = project_round_evidence(
        Hypothesis(hypothesis_id="H-1", plan=_plan("H-1"), started_round=1),
        record,
        prior_rounds=[],
        space=MetricSpace(),
    )

    assert projected.resolution is HypothesisResolution.INCONCLUSIVE


@pytest.mark.parametrize(
    "direction",
    ["max", "min"],
)
def test_reprojection_uses_the_stored_space_for_resolution_and_retention(
    direction: Literal["max", "min"],
) -> None:
    baseline = _legacy_evidence_round(1, 100.0)
    regression = _legacy_evidence_round(2, 90.0, parent_round=1)
    state = AgentRunState(
        metrics=MetricSpace(objectives=(Objective(name="ops", direction=direction),)),
        hypotheses=[
            Hypothesis(
                hypothesis_id="H-1",
                plan=_plan("H-1"),
                started_round=1,
                rounds=[baseline, regression],
            )
        ],
    )

    reprojected = reproject_run_evidence(state)

    hypothesis = reprojected.by_id("H-1")
    assert hypothesis is not None
    assert hypothesis.resolution is (
        HypothesisResolution.DISPROVEN if direction == "max" else HypothesisResolution.PROVEN
    )
    assert hypothesis.candidate_retained is (direction == "min")
    assert hypothesis.measurement is not None
    assert hypothesis.measurement.direction == direction


def test_metric_baseline_prefers_exact_parent_commit_and_fails_closed() -> None:
    parent = _round(1, 100.0, hypothesis_id="parent")
    later = _round(2, 120.0, hypothesis_id="later")

    assert (
        metric_baseline(
            parent_round=2,
            parent_commit=parent.commit,
            metric="total_ops_per_sec",
            rounds=[parent, later],
        )
        is parent
    )
    assert (
        metric_baseline(
            parent_round=2,
            parent_commit="missing",
            metric="total_ops_per_sec",
            rounds=[parent, later],
        )
        is None
    )


def _declared(comparison: MetricComparison | None) -> ResolutionEvidence:
    return ResolutionEvidence(
        declared=HypothesisOutcome.NOMINATED,
        passed=True,
        reviewed=True,
        comparison=comparison,
        benchmark_expected=True,
    )


def test_resolution_consumes_the_comparison_rather_than_the_readings() -> None:
    """Resolution never sees a value, an axis direction, or a tolerance."""
    assert resolve_hypothesis_outcome(_declared(MetricComparison.BETTER)) is (
        HypothesisResolution.PROVEN
    )
    assert resolve_hypothesis_outcome(_declared(MetricComparison.WORSE)) is (
        HypothesisResolution.DISPROVEN
    )
    assert resolve_hypothesis_outcome(_declared(MetricComparison.WITHIN_NOISE)) is (
        HypothesisResolution.INCONCLUSIVE
    )
    assert resolve_hypothesis_outcome(_declared(MetricComparison.INCOMPARABLE)) is (
        HypothesisResolution.INCONCLUSIVE
    )
    # ``None`` is a different fact: no official metric was recorded at all, so
    # a nomination stays undecided rather than being ruled against.
    assert resolve_hypothesis_outcome(_declared(None)) is HypothesisResolution.INCONCLUSIVE


def test_retention_consumes_the_comparison_against_the_best_prior() -> None:
    space = MetricSpace(objectives=(Objective(name="ops", direction="max"),), relative_noise=0.005)
    candidate = Measurement(metric="ops", value=100.2)
    prior = [Measurement(metric="ops", value=100.0)]

    assert scalar_candidate_retained(space.compare_to_best(candidate, prior)) is False
    assert scalar_candidate_retained(space.compare_to_best(candidate, [])) is True
    assert scalar_candidate_retained(space.compare_to_best(None, prior)) is None


_NOISY_OPS = MetricSpace(
    objectives=(Objective(name="total_ops_per_sec", direction="max"),),
    relative_noise=0.05,
)


def _within_noise_run() -> AgentRunState:
    """Two official rounds one percent apart, in a five percent metric space."""
    state = AgentRunState(metrics=_NOISY_OPS)
    baseline = append_round(
        start_hypothesis(state, _plan("H-base"), started_round=1),
        _round(1, 100.0, hypothesis_id="H-base"),
        keep_active=False,
    )
    started = start_hypothesis(
        baseline,
        _plan("H-1"),
        started_round=2,
        parent_round=1,
        parent_commit=f"{1:040x}",
    )
    return append_round(
        started,
        _round(
            2,
            101.0,
            hypothesis_id="H-1",
            parent_round=1,
            parent_commit=f"{1:040x}",
            outcome="inconclusive",
            comparison=MetricComparison.WITHIN_NOISE,
        ),
        keep_active=False,
    )


def test_append_round_resolves_a_within_noise_delta_as_inconclusive() -> None:
    """Regression for #507: a 1% delta under a 5% model is not a proven result.

    ``append_round`` is the loop's own writer, so a tolerance it cannot reach
    silently records the opposite verdict from the one the round observed.
    """
    hypothesis = _within_noise_run().by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.measurement is not None
    assert hypothesis.measurement.delta_pct == pytest.approx(1.0)
    assert hypothesis.resolution is HypothesisResolution.INCONCLUSIVE


def test_resume_reprojection_agrees_with_the_recorded_round_outcome() -> None:
    """Regression for #507: ``--resume`` must not contradict the round record."""
    completed = _within_noise_run()
    live = completed.by_id("H-1")
    resumed = reproject_run_evidence(completed).by_id("H-1")

    assert live is not None
    assert resumed is not None
    assert resumed.resolution is live.resolution
    assert [record.hypothesis_outcome for record in resumed.rounds] == ["inconclusive"]
    assert resumed.resolution is HypothesisResolution.INCONCLUSIVE


def test_retention_and_resolution_share_one_tolerance_boundary() -> None:
    """The chosen semantics: tolerance is ``abs(baseline) * relative_noise``.

    Retention and resolution used to scale the tolerance differently. They now
    ask the same space the same question, boundary included: a delta exactly at
    the tolerance is not a result.
    """
    space = MetricSpace(objectives=(Objective(name="ops", direction="max"),), relative_noise=0.05)
    baseline = [Measurement(metric="ops", value=100.0)]
    at_tolerance = space.compare_to_best(Measurement(metric="ops", value=105.0), baseline)
    beyond = space.compare_to_best(Measurement(metric="ops", value=105.01), baseline)

    assert resolve_hypothesis_outcome(_declared(at_tolerance)) is (
        HypothesisResolution.INCONCLUSIVE
    )
    assert scalar_candidate_retained(at_tolerance) is False
    assert resolve_hypothesis_outcome(_declared(beyond)) is HypothesisResolution.PROVEN
    assert scalar_candidate_retained(beyond) is True


def test_adopting_a_metric_space_rewrites_the_stored_space_and_evidence() -> None:
    """The run's launch configuration is written once; readers take it from state."""
    strict_run = _strip_stored_comparisons(
        _within_noise_run().model_copy(
            update={
                "metrics": MetricSpace(
                    objectives=(Objective(name="total_ops_per_sec", direction="max"),)
                )
            },
            deep=True,
        )
    )
    reprojected = reproject_run_evidence(strict_run).by_id("H-1")
    assert reprojected is not None
    assert reprojected.resolution is HypothesisResolution.PROVEN

    adopted = adopt_metric_space(strict_run, _NOISY_OPS)
    hypothesis = adopted.by_id("H-1")

    assert adopted.metrics == _NOISY_OPS
    assert hypothesis is not None
    assert hypothesis.resolution is HypothesisResolution.INCONCLUSIVE


def test_state_written_before_the_metric_space_loads_as_the_empty_strict_space() -> None:
    legacy = AgentRunState.model_validate({"schema_version": 1, "hypotheses": []})

    assert legacy.metrics == MetricSpace()


def test_a_record_without_a_stored_comparison_is_re_derived_from_the_space() -> None:
    """Compatibility path for rounds written before the comparison was stored."""
    stripped = _strip_stored_comparisons(_within_noise_run())
    assert [record.perf_comparison for record in stripped.rounds] == [None, None]

    hypothesis = reproject_run_evidence(stripped).by_id("H-1")

    assert hypothesis is not None
    assert hypothesis.resolution is HypothesisResolution.INCONCLUSIVE


def test_a_stored_comparison_survives_a_space_whose_tolerance_changed() -> None:
    """The round answers for itself, so a re-configured space cannot rewrite it.

    Editing ``objectives.toml`` between resumes changes how later rounds are
    ordered, not how recorded ones were: a delta the run judged to be noise
    stays noise. Stripping the stored answer is what puts a round back under
    the space's control.
    """
    recorded = _within_noise_run()
    strict = MetricSpace(objectives=_NOISY_OPS.objectives)

    resolved = adopt_metric_space(recorded, strict).by_id("H-1")
    re_derived = adopt_metric_space(_strip_stored_comparisons(recorded), strict).by_id("H-1")

    assert resolved is not None
    assert resolved.resolution is HypothesisResolution.INCONCLUSIVE
    assert re_derived is not None
    assert re_derived.resolution is HypothesisResolution.PROVEN


def _strip_stored_comparisons(state: AgentRunState) -> AgentRunState:
    """Return *state* as a run written before comparisons were persisted."""
    payload = state.model_dump()
    for hypothesis in payload["hypotheses"]:
        for record in hypothesis["rounds"]:
            record["perf_comparison"] = None
    return AgentRunState.model_validate(payload)
