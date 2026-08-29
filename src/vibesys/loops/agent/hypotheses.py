"""Pure transitions for the authoritative agent-run hypothesis state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from vibesys.loops.agent.model import (
    AgentRunState,
    Hypothesis,
    HypothesisMeasurement,
    HypothesisResolution,
    HypothesisReview,
    HypothesisStrategy,
)
from vibesys.schemas import (
    HypothesisOutcome,
    HypothesisStrategyUpdate,
    OrchestratorPlan,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from vs_loop_state import RoundRecord


@dataclass(frozen=True)
class ResolutionEvidence:
    """Inputs the framework needs to finalize one hypothesis declaration.

    ``official_metric`` must carry a trusted framework-owned measurement or
    ``None``; callers never pass an agent self-reported number here.
    """

    declared: HypothesisOutcome | None
    passed: bool
    reviewed: bool
    official_metric: float | None
    baseline_metric: float | None
    direction: Literal["max", "min"] | None
    noise_fraction: float = 0.0


def resolve_hypothesis_outcome(
    evidence: ResolutionEvidence,
) -> HypothesisResolution | None:
    """Resolve one declaration only after review and trusted evidence.

    A supportive declaration (``SUPPORTED``/``NOMINATED``) never resolves
    ``PROVEN`` on the agent's word alone: without a trusted measurement it
    resolves ``UNMEASURED``, and with one the measurement decides.
    """
    if not evidence.reviewed:
        resolution = None
    elif not evidence.passed:
        resolution = HypothesisResolution.REJECTED
    elif evidence.declared is None:
        resolution = None
    else:
        resolution = {
            HypothesisOutcome.DISPROVEN: HypothesisResolution.DISPROVEN,
            HypothesisOutcome.IMPLEMENTATION_FAILED: HypothesisResolution.IMPLEMENTATION_FAILED,
            HypothesisOutcome.INCONCLUSIVE: HypothesisResolution.INCONCLUSIVE,
            HypothesisOutcome.BLOCKED: HypothesisResolution.BLOCKED,
        }.get(evidence.declared)
        if evidence.declared is HypothesisOutcome.CONTINUE:
            resolution = None
        elif resolution is None:
            if evidence.official_metric is None:
                resolution = HypothesisResolution.UNMEASURED
            else:
                resolution = _resolve_metric_evidence(evidence)
    return resolution


def _resolve_metric_evidence(evidence: ResolutionEvidence) -> HypothesisResolution:
    if (
        evidence.official_metric is None
        or evidence.baseline_metric in {None, 0}
        or evidence.direction not in {"max", "min"}
    ):
        return HypothesisResolution.INCONCLUSIVE
    baseline = evidence.baseline_metric
    assert baseline is not None  # noqa: S101  # narrowed by the guard above
    raw_delta = (evidence.official_metric - baseline) / abs(baseline) * 100
    benefit = raw_delta if evidence.direction == "max" else -raw_delta
    tolerance_pct = evidence.noise_fraction * 100
    if benefit < -tolerance_pct:
        return HypothesisResolution.DISPROVEN
    if benefit > tolerance_pct:
        return HypothesisResolution.PROVEN
    return HypothesisResolution.INCONCLUSIVE


def scalar_candidate_retained(
    *,
    metric: float | None,
    direction: Literal["max", "min"] | None,
    prior: Sequence[float],
    noise_fraction: float = 0.0,
) -> bool | None:
    """Return whether an official scalar candidate advances the best checkpoint."""
    if metric is None or direction not in {"max", "min"}:
        return None
    if not prior:
        return True
    best = max(prior) if direction == "max" else min(prior)
    scale = abs(best) if best != 0 else 1.0
    improvement = metric - best if direction == "max" else best - metric
    return improvement > scale * noise_fraction


def metric_baseline(
    *,
    parent_round: int | None,
    parent_commit: str | None,
    metric: str | None,
    rounds: Sequence[RoundRecord],
) -> RoundRecord | None:
    """Find the baseline for one metric, preferring the exact causal parent.

    The parent of an official round is usually a provisional round whenever
    ``official_eval_every`` exceeds 1, so an exact parent match rarely exists.
    Fall back to the newest trusted official measurement that does not
    postdate the parent round; comparing against a later measurement would
    invert cause and effect.
    """
    comparable = [
        item
        for item in rounds
        if item.official_evaluation
        and item.perf_metric is not None
        and _trusted_measurement(item)
        and (item.perf_unit == metric or (metric is not None and metric in item.metrics))
    ]
    if not comparable:
        return None
    if parent_commit is not None:
        exact = next(
            (item for item in reversed(comparable) if item.commit == parent_commit),
            None,
        )
        if exact is not None:
            return exact
    if parent_round is not None:
        return next(
            (item for item in reversed(comparable) if item.round_number <= parent_round),
            None,
        )
    return comparable[-1]


def start_hypothesis(
    state: AgentRunState,
    plan: OrchestratorPlan,
    *,
    started_round: int,
    parent_round: int | None = None,
    parent_commit: str | None = None,
) -> AgentRunState:
    """Start a new hypothesis after applying its strategic updates."""
    if state.active_hypothesis_id is not None:
        raise ValueError("cannot start a hypothesis while another is active")  # noqa: TRY003
    identifier = plan.hypothesis_id.strip()
    if not identifier:
        raise ValueError("hypothesis ID must not be blank")  # noqa: TRY003
    if state.by_id(identifier) is not None:
        raise ValueError(f"hypothesis ID {identifier!r} already exists")  # noqa: TRY003
    updated = apply_strategy_updates(state, plan.hypothesis_updates)
    updated.hypotheses.append(
        Hypothesis(
            hypothesis_id=identifier,
            plan=plan.model_copy(update={"hypothesis_id": identifier}, deep=True),
            started_round=started_round,
            parent_round=parent_round,
            parent_commit=parent_commit,
        )
    )
    updated.active_hypothesis_id = identifier
    return _validated_state(updated)


def update_active_hypothesis(
    state: AgentRunState,
    hypothesis: Hypothesis,
) -> AgentRunState:
    """Replace the active hypothesis with an updated restart checkpoint."""
    identifier = state.active_hypothesis_id
    if identifier is None:
        raise ValueError("cannot update an active hypothesis when none is active")  # noqa: TRY003
    if hypothesis.hypothesis_id != identifier:
        raise ValueError("updated hypothesis must preserve the active hypothesis ID")  # noqa: TRY003
    updated = state.clone()
    index = next(
        index for index, item in enumerate(updated.hypotheses) if item.hypothesis_id == identifier
    )
    updated.hypotheses[index] = hypothesis.model_copy(deep=True)
    return _validated_state(updated)


def append_round(
    state: AgentRunState,
    record: RoundRecord,
    *,
    keep_active: bool,
    legacy_directions: Mapping[str, Literal["max", "min"]] | None = None,
) -> AgentRunState:
    """Append one completed round to the active hypothesis."""
    active = state.active_hypothesis
    if active is None:
        raise ValueError("cannot append a round when no hypothesis is active")  # noqa: TRY003
    if record.hypothesis_id != active.hypothesis_id:
        raise ValueError("round hypothesis_id must match the active hypothesis")  # noqa: TRY003
    if any(item.round_number == record.round_number for item in state.rounds):
        raise ValueError(f"round {record.round_number} already exists")  # noqa: TRY003

    updated = state.clone()
    updated_active = updated.active_hypothesis
    assert updated_active is not None  # noqa: S101  # preserved by the clone
    projected = project_round_evidence(
        updated_active,
        record,
        prior_rounds=state.rounds,
        legacy_directions=legacy_directions,
    )
    index = next(
        index
        for index, item in enumerate(updated.hypotheses)
        if item.hypothesis_id == projected.hypothesis_id
    )
    updated.hypotheses[index] = projected
    if not keep_active:
        updated.active_hypothesis_id = None
    return _validated_state(updated)


def finish_hypothesis(state: AgentRunState) -> AgentRunState:
    """Clear the active pointer without changing the hypothesis itself."""
    if state.active_hypothesis_id is None:
        return state.clone()
    updated = state.clone()
    updated.active_hypothesis_id = None
    return _validated_state(updated)


def apply_strategy_updates(
    state: AgentRunState,
    updates: Sequence[HypothesisStrategyUpdate],
) -> AgentRunState:
    """Apply orchestrator-owned parked/abandoned decisions."""
    updated = state.clone()
    seen: set[str] = set()
    for change in updates:
        if change.hypothesis_id in seen:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"duplicate strategy update for hypothesis {change.hypothesis_id!r}"
            )
        seen.add(change.hypothesis_id)
        index = next(
            (
                index
                for index, item in enumerate(updated.hypotheses)
                if item.hypothesis_id == change.hypothesis_id
            ),
            None,
        )
        if index is None:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"strategy update names unknown hypothesis {change.hypothesis_id!r}"
            )
        item = updated.hypotheses[index]
        if updated.active_hypothesis_id == change.hypothesis_id:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"cannot {change.disposition} active hypothesis {change.hypothesis_id!r}"
            )
        if not item.rounds:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"cannot update incomplete hypothesis {change.hypothesis_id!r}"
            )
        item.strategy = HypothesisStrategy(change.disposition)
        item.strategy_reason = change.reason.strip()
    return _validated_state(updated)


def project_round_evidence(
    hypothesis: Hypothesis,
    record: RoundRecord,
    *,
    prior_rounds: Sequence[RoundRecord],
    legacy_directions: Mapping[str, Literal["max", "min"]] | None = None,
) -> Hypothesis:
    """Return a hypothesis updated with one completed round's evidence."""
    if record.hypothesis_id != hypothesis.hypothesis_id:
        raise ValueError("round hypothesis_id must match its owning hypothesis")  # noqa: TRY003
    if any(item.round_number == record.round_number for item in hypothesis.rounds):
        raise ValueError(f"round {record.round_number} already belongs to hypothesis")  # noqa: TRY003
    updated = hypothesis.clone()
    updated.rounds.append(record)
    updated.declared_outcome = _declared_outcome(record.hypothesis_declared_outcome)
    updated.review = _review(record)
    measurement = _measurement(record, prior_rounds, legacy_directions)
    if record.judge_verdict is not None:
        updated.resolution = resolve_hypothesis_outcome(
            ResolutionEvidence(
                declared=updated.declared_outcome,
                passed=record.passed,
                reviewed=updated.review
                not in {HypothesisReview.PENDING, HypothesisReview.DEFERRED},
                official_metric=(
                    record.perf_metric
                    if record.official_evaluation and _trusted_measurement(record)
                    else None
                ),
                baseline_metric=(measurement.baseline_value if measurement is not None else None),
                direction=(
                    measurement.direction if measurement is not None else record.perf_direction
                ),
            )
        )
    else:
        updated.resolution = (
            _resolution(record.hypothesis_outcome)
            if updated.review not in {HypothesisReview.PENDING, HypothesisReview.DEFERRED}
            else None
        )
    if measurement is not None:
        updated.measurement = measurement
    retained = _retained(record, prior_rounds, legacy_directions)
    if retained is not None:
        updated.candidate_retained = retained
    if record.judge_verdict is None:
        _correct_legacy_resolution(updated, record, measurement)
    return Hypothesis.model_validate(updated.model_dump())


def reproject_run_evidence(
    state: AgentRunState,
    *,
    legacy_directions: Mapping[str, Literal["max", "min"]] | None = None,
) -> AgentRunState:
    """Rebuild hypothesis summaries from their authoritative round evidence."""
    updated = state.clone()
    updated.hypotheses = [
        hypothesis.model_copy(
            update={
                "rounds": [],
                "declared_outcome": None,
                "review": HypothesisReview.PENDING,
                "resolution": None,
                "measurement": None,
                "candidate_retained": None,
            },
            deep=True,
        )
        for hypothesis in updated.hypotheses
    ]
    prior_rounds: list[RoundRecord] = []
    for record in state.rounds:
        index = next(
            (
                index
                for index, hypothesis in enumerate(updated.hypotheses)
                if hypothesis.hypothesis_id == record.hypothesis_id
            ),
            None,
        )
        if index is None:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"round {record.round_number} names unknown hypothesis {record.hypothesis_id!r}"
            )
        updated.hypotheses[index] = project_round_evidence(
            updated.hypotheses[index],
            record,
            prior_rounds=prior_rounds,
            legacy_directions=legacy_directions,
        )
        prior_rounds.append(record)
    return _validated_state(updated)


def _validated_state(state: AgentRunState) -> AgentRunState:
    return AgentRunState.model_validate(state.model_dump())


def _correct_legacy_resolution(
    hypothesis: Hypothesis,
    record: RoundRecord,
    measurement: HypothesisMeasurement | None,
) -> None:
    if record.judge_verdict is not None or hypothesis.resolution is not HypothesisResolution.PROVEN:
        return
    if (
        record.official_evaluation
        and record.perf_metric is not None
        and (measurement is None or measurement.direction is None or measurement.delta_pct is None)
    ):
        hypothesis.resolution = HypothesisResolution.INCONCLUSIVE
        return
    if measurement is None:
        return
    assert measurement.delta_pct is not None  # noqa: S101  # guarded above
    benefit = measurement.delta_pct
    if measurement.direction == "min":
        benefit = -benefit
    if benefit < 0:
        hypothesis.resolution = HypothesisResolution.DISPROVEN
    elif benefit == 0:
        hypothesis.resolution = HypothesisResolution.INCONCLUSIVE


def _declared_outcome(value: str | None) -> HypothesisOutcome | None:
    if value is None:
        return None
    try:
        return HypothesisOutcome(value)
    except ValueError:
        return None


def _review(record: RoundRecord) -> HypothesisReview:
    if record.judge_verdict is not None:
        return HypothesisReview(record.judge_verdict)
    if not record.reviewed:
        return HypothesisReview.DEFERRED
    return HypothesisReview.PASS if record.passed else HypothesisReview.FAIL


def _resolution(value: str | None) -> HypothesisResolution | None:
    if value is None or value == HypothesisOutcome.CONTINUE.value:
        return None
    if value in {HypothesisOutcome.SUPPORTED.value, HypothesisOutcome.NOMINATED.value}:
        return None
    try:
        return HypothesisResolution(value)
    except ValueError:
        return HypothesisResolution.INCONCLUSIVE


def _trusted_measurement(record: RoundRecord) -> bool:
    """Whether ``record.perf_metric`` came from a framework-owned gate.

    Legacy records carry no provenance and stay trusted so reprojection does
    not rewrite their historical resolutions; only an explicit agent
    self-report is untrusted.
    """
    return record.perf_provenance != "implementer"


def _measurement(
    record: RoundRecord,
    prior_rounds: Sequence[RoundRecord],
    legacy_directions: Mapping[str, Literal["max", "min"]] | None,
) -> HypothesisMeasurement | None:
    if (
        not record.official_evaluation
        or record.perf_metric is None
        or record.perf_unit is None
        or not _trusted_measurement(record)
    ):
        return None
    direction = record.perf_direction or _configured_legacy_direction(record, legacy_directions)
    baseline = _baseline(record, prior_rounds)
    baseline_value = record.perf_baseline_metric
    if baseline_value is None and baseline is not None:
        baseline_value = _baseline_metric_value(baseline, record.perf_unit)
    delta = record.perf_delta_pct
    if delta is None and baseline_value not in {None, 0}:
        assert baseline_value is not None  # noqa: S101  # narrowed above
        delta = (record.perf_metric - baseline_value) / abs(baseline_value) * 100
    return HypothesisMeasurement(
        round=record.round_number,
        metric=record.perf_unit,
        value=record.perf_metric,
        unit=record.perf_unit,
        direction=direction,
        baseline_round=(
            record.perf_baseline_round
            if record.perf_baseline_round is not None
            else baseline.round_number
            if baseline is not None
            else None
        ),
        baseline_commit=record.perf_baseline_commit or record.hypothesis_parent_commit,
        baseline_value=baseline_value,
        delta_pct=delta,
    )


def _baseline(record: RoundRecord, prior_rounds: Sequence[RoundRecord]) -> RoundRecord | None:
    return metric_baseline(
        parent_round=record.hypothesis_parent_round,
        parent_commit=record.hypothesis_parent_commit,
        metric=record.perf_unit,
        rounds=prior_rounds,
    )


def _baseline_metric_value(baseline: RoundRecord, metric: str | None) -> float | None:
    """Read a baseline's value for *metric*, tolerating a renamed headline unit."""
    if metric is not None and metric in baseline.metrics:
        return baseline.metrics[metric]
    return baseline.perf_metric


def _retained(
    record: RoundRecord,
    prior_rounds: Sequence[RoundRecord],
    legacy_directions: Mapping[str, Literal["max", "min"]] | None,
) -> bool | None:
    if record.candidate_retained is not None:
        retained = record.candidate_retained
    elif record.judge_verdict is not None:
        retained = None
    elif record.candidate_disposition in {"pareto_frontier", "prerequisite"}:
        retained = True
    elif record.candidate_disposition == "discard":
        retained = False
    elif not record.official_evaluation or record.perf_metric is None:
        retained = None
    else:
        direction = record.perf_direction or _configured_legacy_direction(
            record,
            legacy_directions,
        )
        comparable = [
            prior.perf_metric
            for prior in prior_rounds
            if prior.official_evaluation
            and prior.passed
            and prior.perf_metric is not None
            and _trusted_measurement(prior)
            and prior.perf_unit == record.perf_unit
        ]
        retained = scalar_candidate_retained(
            metric=record.perf_metric,
            direction=direction,
            prior=comparable,
        )
    return retained


def _configured_legacy_direction(
    record: RoundRecord,
    directions: Mapping[str, Literal["max", "min"]] | None,
) -> Literal["max", "min"] | None:
    if record.judge_verdict is not None or record.perf_unit is None or directions is None:
        return None
    return directions.get(record.perf_unit)
