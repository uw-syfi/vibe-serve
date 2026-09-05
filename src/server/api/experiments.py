"""Project authoritative agent-run state into experiment-log protocol entries.

The agent loop owns hypothesis lifecycle state. This module is deliberately a
one-way projection: it does not group rounds, select a baseline, or infer a
resolution from individual round fields.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from server.api.protocol import HypothesisEntry, HypothesisRound
from vibesys.loops.agent.hypotheses import measurement_delta_reason
from vibesys.loops.agent.model import HypothesisResolution
from vibesys.schemas import CandidateDisposition, HypothesisOutcome, derive_hypothesis_title

if TYPE_CHECKING:
    from vibesys.loops.agent.model import AgentRunState, Hypothesis
    from vs_loop_state import RoundRecord


def build_experiment_log(state: AgentRunState) -> list[HypothesisEntry]:
    """Return the complete hypothesis history in stable start-round order."""
    return sorted(
        (
            _entry(hypothesis, active_id=state.active_hypothesis_id)
            for hypothesis in state.hypotheses
        ),
        key=lambda entry: (entry.first_round, entry.hypothesis_id),
    )


def _entry(hypothesis: Hypothesis, *, active_id: str | None) -> HypothesisEntry:
    """Copy one domain hypothesis into its presentation-neutral DTO."""
    rounds = hypothesis.rounds
    measurement = hypothesis.measurement
    return HypothesisEntry(
        hypothesis_id=hypothesis.hypothesis_id,
        title=_text(hypothesis.plan.title) or derive_hypothesis_title(hypothesis.plan.hypothesis),
        claim=_text(hypothesis.plan.hypothesis),
        action=_text(hypothesis.plan.task),
        first_round=hypothesis.started_round,
        last_round=rounds[-1].round_number if rounds else hypothesis.started_round,
        rounds=[_round(record) for record in rounds],
        resolved_outcome=(
            hypothesis.resolution.value if hypothesis.resolution is not None else None
        ),
        judge_verdict=_judge_verdict(hypothesis),
        # The measurement fields are intentionally copied as one tuple.
        # Choosing a newer per-round metric here would pair it with a
        # different causal delta and make the UI lie about the measurement.
        perf_metric=measurement.value if measurement is not None else None,
        perf_unit=_text(measurement.unit) if measurement is not None else None,
        perf_delta_pct=measurement.delta_pct if measurement is not None else None,
        perf_metric_name=_text(measurement.metric) if measurement is not None else None,
        perf_direction=measurement.direction if measurement is not None else None,
        perf_baseline_value=measurement.baseline_value if measurement is not None else None,
        perf_baseline_round=measurement.baseline_round if measurement is not None else None,
        perf_baseline_commit=(
            _text(measurement.baseline_commit) if measurement is not None else None
        ),
        perf_delta_reason=measurement_delta_reason(hypothesis),
        kept=hypothesis.candidate_retained,
        strategy_disposition=hypothesis.strategy.value,
        strategy_reason=hypothesis.strategy_reason,
        active=hypothesis.hypothesis_id == active_id,
    )


def _round(record: RoundRecord) -> HypothesisRound:
    return HypothesisRound(
        round=record.round_number,
        passed=record.passed,
        reviewed=record.reviewed,
        hypothesis_outcome=_outcome(record.hypothesis_outcome),
        judge_verdict=record.judge_verdict,
        perf_metric=record.perf_metric,
        perf_unit=_text(record.perf_unit),
        perf_delta_pct=record.perf_delta_pct,
        commit=_text(record.commit),
        official_evaluation=record.official_evaluation,
        candidate_disposition=_disposition(record.candidate_disposition),
    )


def _outcome(value: str | None) -> HypothesisOutcome | HypothesisResolution | None:
    """Read a stored outcome as one of the two vocabularies that produce it.

    A round record holds the implementer's declared outcome unless the
    framework resolved the hypothesis, in which case it holds the resolution
    instead. Anything else is a legacy or retired value with no meaning for a
    client, so it projects as "not recorded" rather than failing the log.
    """
    if not value:
        return None
    for vocabulary in (HypothesisOutcome, HypothesisResolution):
        member = vocabulary.__members__.get(value.upper())
        if member is not None and member.value == value:
            return member
    return None


def _disposition(value: str | None) -> CandidateDisposition | None:
    """Read a stored disposition, dropping values the framework retired."""
    if not value:
        return None
    member = CandidateDisposition.__members__.get(value.upper())
    return member if member is not None and member.value == value else None


def _judge_verdict(hypothesis: Hypothesis) -> Literal["pass", "fail"] | None:
    value = hypothesis.review.value
    return value if value in ("pass", "fail") else None


def _text(value: str | None) -> str | None:
    return value or None
