"""Pure tests for the agent-loop state model."""

import math

import pytest
from pydantic import ValidationError

from vibesys.loops.agent.model import ActiveHypothesis
from vibesys.schemas import OrchestratorPlan


def _plan() -> OrchestratorPlan:
    return OrchestratorPlan(
        task="optimize the queue",
        pass_criteria="the checker passes",  # noqa: S106
        reasoning="reduce contention",
    )


def test_active_hypothesis_round_trips_through_its_external_schema() -> None:
    state = ActiveHypothesis(
        plan=_plan(),
        started_round=3,
        parent_round=2,
        parent_commit="a" * 40,
        gate_approved_metrics={"throughput": 42.0},
    )

    loaded = ActiveHypothesis.model_validate_json(state.model_dump_json(), strict=True)

    assert loaded == state
    assert loaded.schema_version == 1


def test_active_hypothesis_rejects_unknown_external_fields() -> None:
    state = ActiveHypothesis(plan=_plan(), started_round=1)
    payload = state.model_dump(mode="json") | {"unexpected": True}

    with pytest.raises(ValidationError, match="unexpected"):
        ActiveHypothesis.model_validate(payload, strict=True)


def test_active_hypothesis_rejects_coercion_and_non_finite_metrics() -> None:
    with pytest.raises(ValidationError, match="started_round"):
        ActiveHypothesis.model_validate(
            {"schema_version": 1, "plan": _plan(), "started_round": "1"},
            strict=True,
        )

    with pytest.raises(ValidationError, match="gate_approved_metrics"):
        ActiveHypothesis(
            plan=_plan(),
            started_round=1,
            gate_approved_metrics={"throughput": math.inf},
        )


def test_active_hypothesis_clone_has_independent_nested_state() -> None:
    state = ActiveHypothesis(
        plan=_plan(),
        started_round=1,
        gate_approved_metrics={"throughput": 1.0},
    )

    cloned = state.clone()
    cloned.gate_approved_metrics["throughput"] = 2.0

    assert state.gate_approved_metrics == {"throughput": 1.0}
    assert cloned.gate_approved_metrics == {"throughput": 2.0}
