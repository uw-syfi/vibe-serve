"""Authoritative persisted state models owned by the agent loop."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from vibesys.schemas import (
    CandidateDisposition,
    HypothesisOutcome,
    OrchestratorPlan,
)
from vs_loop_state import RoundRecord


class HypothesisResolution(StrEnum):
    """Framework-owned resolution after all available evidence is known."""

    PROVEN = "proven"
    DISPROVEN = "disproven"
    INCONCLUSIVE = "inconclusive"
    IMPLEMENTATION_FAILED = "implementation_failed"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    # The review passed but no trusted framework measurement exists, so the
    # empirical claim is neither proven nor failed. Distinct from INCONCLUSIVE,
    # which reports a trusted measurement that could not decide the claim.
    UNMEASURED = "unmeasured"


class HypothesisReview(StrEnum):
    """Independent review state, separate from empirical resolution."""

    PENDING = "pending"
    PASS = "pass"  # noqa: S105  # tracked: #288
    FAIL = "fail"
    DEFERRED = "deferred"


class HypothesisStrategy(StrEnum):
    """Orchestrator-owned strategic treatment of a research direction."""

    AVAILABLE = "available"
    PARKED = "parked"
    ABANDONED = "abandoned"


class HypothesisMeasurement(BaseModel):
    """Official headline measurement and its causal comparison baseline."""

    model_config = ConfigDict(extra="forbid", strict=True)

    round: Annotated[int, Field(gt=0)]
    metric: str = Field(min_length=1)
    value: FiniteFloat
    unit: str | None = None
    direction: Literal["max", "min"] | None = None
    baseline_round: Annotated[int, Field(gt=0)] | None = None
    baseline_commit: str | None = None
    baseline_value: FiniteFloat | None = None
    delta_pct: FiniteFloat | None = None


class Hypothesis(BaseModel):
    """One hypothesis, including its plan, round evidence, and restart state."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    hypothesis_id: str = Field(min_length=1)
    plan: OrchestratorPlan
    started_round: Annotated[int, Field(gt=0)]
    parent_round: Annotated[int, Field(gt=0)] | None = None
    parent_commit: str | None = None
    rounds: list[RoundRecord] = Field(default_factory=list)

    feedback: str | None = None
    next_step: str | None = None
    continuation_rounds: Annotated[int, Field(ge=0)] = 0
    revert_applied: bool = False
    revert_commit: str | None = None
    gate_revalidation_pending: bool = False
    gate_approved_perf_metric: FiniteFloat | None = None
    gate_approved_perf_unit: str | None = None
    gate_approved_metrics: dict[str, FiniteFloat] = Field(default_factory=dict)
    gate_approved_evaluation_artifact: str | None = None
    gate_approved_candidate_disposition: str = CandidateDisposition.UNASSESSED.value
    gate_approved_candidate_metrics: dict[str, FiniteFloat] = Field(default_factory=dict)
    gate_approved_candidate_evaluation_artifact: str | None = None
    gate_approved_candidate_operating_point: str = ""
    gate_approved_candidate_retention_reason: str = ""
    gate_candidate_commit: str | None = None
    gate_accuracy_passed: bool = False

    declared_outcome: HypothesisOutcome | None = None
    review: HypothesisReview = HypothesisReview.PENDING
    resolution: HypothesisResolution | None = None
    measurement: HypothesisMeasurement | None = None
    candidate_retained: bool | None = None
    strategy: HypothesisStrategy = HypothesisStrategy.AVAILABLE
    strategy_reason: str | None = None

    @model_validator(mode="after")
    def _valid_identity(self) -> Self:
        if self.plan.hypothesis_id != self.hypothesis_id:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                "plan hypothesis_id must match its owning hypothesis"
            )
        round_numbers = [record.round_number for record in self.rounds]
        if round_numbers != sorted(set(round_numbers)):
            raise ValueError(  # noqa: TRY003  # tracked: #288
                "hypothesis rounds must be unique and ordered"
            )
        if any(record.hypothesis_id != self.hypothesis_id for record in self.rounds):
            raise ValueError(  # noqa: TRY003  # tracked: #288
                "round hypothesis_id must match its owning hypothesis"
            )
        return self

    def clone(self) -> Hypothesis:
        """Return an independent copy for computing the next state."""
        return self.model_copy(deep=True)


class AgentRunState(BaseModel):
    """The single authoritative state aggregate for an agent-loop run."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[1] = 1
    active_hypothesis_id: str | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid_identity(self) -> Self:
        identifiers = [item.hypothesis_id for item in self.hypotheses]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("hypothesis IDs must be unique")  # noqa: TRY003  # tracked: #288
        if self.active_hypothesis_id is not None:
            active = self.by_id(self.active_hypothesis_id)
            if active is None:
                raise ValueError(  # noqa: TRY003  # tracked: #288
                    "active_hypothesis_id must name a known hypothesis"
                )
            if active.strategy is not HypothesisStrategy.AVAILABLE:
                raise ValueError(  # noqa: TRY003  # tracked: #288
                    "the active hypothesis must be strategically available"
                )
        round_numbers = [
            record.round_number for hypothesis in self.hypotheses for record in hypothesis.rounds
        ]
        if len(set(round_numbers)) != len(round_numbers):
            raise ValueError("round numbers must be globally unique")  # noqa: TRY003  # tracked: #288
        return self

    def by_id(self, hypothesis_id: str) -> Hypothesis | None:
        """Return a detached hypothesis copy by stable ID."""
        hypothesis = next(
            (item for item in self.hypotheses if item.hypothesis_id == hypothesis_id),
            None,
        )
        return hypothesis.model_copy(deep=True) if hypothesis is not None else None

    @property
    def active_hypothesis(self) -> Hypothesis | None:
        """Return a detached copy of the active hypothesis, if any."""
        if self.active_hypothesis_id is None:
            return None
        return self.by_id(self.active_hypothesis_id)

    @property
    def rounds(self) -> list[RoundRecord]:
        """Return completed rounds in global chronological order."""
        return sorted(
            (record for hypothesis in self.hypotheses for record in hypothesis.rounds),
            key=lambda record: record.round_number,
        )

    def clone(self) -> AgentRunState:
        """Return an independent copy for computing the next state."""
        return self.model_copy(deep=True)
