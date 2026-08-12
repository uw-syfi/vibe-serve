"""Tests for typed round records, their codec, and rollback resolution."""

import pytest
from pydantic import ValidationError

from vs_loop_state import (
    RoundHistory,
    RoundRecord,
    parse_round_record,
    serialize_round_record,
)


def _record(
    round_number: int,
    commit: str | None,
    *,
    outcome: str | None = None,
    parent_round: int | None = None,
    parent_commit: str | None = None,
) -> RoundRecord:
    return RoundRecord(
        round_number=round_number,
        commit=commit,
        perf_metric=None,
        perf_unit=None,
        passed=True,
        hypothesis_outcome=outcome,
        hypothesis_parent_round=parent_round,
        hypothesis_parent_commit=parent_commit,
    )


def test_public_round_record_codec_round_trips_current_schema() -> None:
    record = RoundRecord(
        round_number=3,
        commit="a" * 40,
        perf_metric=1.0,
        perf_unit="ops/s",
        passed=True,
        official_evaluation=True,
        official_evaluation_reason="cadence",
        metrics={"throughput": 1.0},
        candidate_disposition="retained",
        candidate_metrics={"latency": 2.0},
    )

    payload = serialize_round_record(record)

    assert payload["round"] == 3
    assert "round_number" not in payload
    assert parse_round_record(payload) == record


def test_parse_round_record_applies_declared_defaults() -> None:
    record = parse_round_record(
        {
            "round": 1,
            "commit": None,
            "perf_metric": None,
            "perf_unit": None,
            "passed": False,
        }
    )

    assert record.profile_skipped is False
    assert record.reviewed is True
    assert record.official_evaluation is False
    assert record.candidate_disposition == "unassessed"


def test_parse_round_record_preserves_explicit_official_evaluation() -> None:
    record = parse_round_record(
        {
            "round": 1,
            "commit": "a" * 40,
            "perf_metric": None,
            "perf_unit": None,
            "passed": True,
            "hypothesis_outcome": "proven",
            "official_evaluation": False,
        }
    )

    assert record.official_evaluation is False


def test_parse_round_record_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        parse_round_record(
            {
                "round": 1,
                "commit": None,
                "perf_metric": None,
                "perf_unit": None,
                "passed": False,
                "made_up_field": "surprise",
            }
        )


def test_round_history_starts_empty_and_appends_records() -> None:
    history = RoundHistory()
    first = _record(1, "a" * 40)
    second = _record(2, "b" * 40)

    history.append(first)
    history.append(second)

    assert history.records == [first, second]


def test_rollback_with_no_history_returns_target_commit_unchanged() -> None:
    target = _record(5, "a" * 40)

    commit, child_round = RoundHistory().resolve_rollback_commit(target, {"disproven"})

    assert commit == "a" * 40
    assert child_round is None


@pytest.mark.parametrize("failed_outcome", ["disproven", "implementation_failed"])
def test_failed_latest_child_rollback_uses_its_exact_parent_commit(
    failed_outcome: str,
) -> None:
    historical_parent = _record(20, "a" * 40)
    failed_child = _record(
        21,
        "c" * 40,
        outcome=failed_outcome,
        parent_round=20,
        parent_commit="b" * 40,
    )
    history = RoundHistory(records=[historical_parent, failed_child])

    commit, child_round = history.resolve_rollback_commit(
        historical_parent,
        {"disproven", "implementation_failed"},
    )

    assert commit == "b" * 40
    assert child_round == 21


def test_rollback_ignores_outcomes_outside_the_failure_set() -> None:
    historical_parent = _record(20, "a" * 40)
    continuing_child = _record(
        21,
        "c" * 40,
        outcome="continue",
        parent_round=20,
        parent_commit="b" * 40,
    )
    history = RoundHistory(records=[historical_parent, continuing_child])

    commit, child_round = history.resolve_rollback_commit(
        historical_parent,
        {"disproven", "implementation_failed"},
    )

    assert commit == "a" * 40
    assert child_round is None


def test_rollback_requires_the_failed_child_to_be_latest() -> None:
    historical_parent = _record(20, "a" * 40)
    failed_child = _record(
        21,
        "c" * 40,
        outcome="disproven",
        parent_round=20,
        parent_commit="b" * 40,
    )
    later_round = _record(22, "d" * 40)
    history = RoundHistory(records=[historical_parent, failed_child, later_round])

    commit, child_round = history.resolve_rollback_commit(historical_parent, {"disproven"})

    assert commit == "a" * 40
    assert child_round is None


def test_rollback_requires_the_failed_childs_parent_commit() -> None:
    historical_parent = _record(20, "a" * 40)
    failed_child = _record(
        21,
        "c" * 40,
        outcome="disproven",
        parent_round=20,
    )
    history = RoundHistory(records=[historical_parent, failed_child])

    commit, child_round = history.resolve_rollback_commit(historical_parent, {"disproven"})

    assert commit == "a" * 40
    assert child_round is None


def test_distant_rollback_uses_requested_historical_commit() -> None:
    historical_target = _record(5, "a" * 40)
    failed_child = _record(
        21,
        "c" * 40,
        outcome="disproven",
        parent_round=20,
        parent_commit="b" * 40,
    )
    history = RoundHistory(records=[historical_target, failed_child])

    commit, child_round = history.resolve_rollback_commit(historical_target, {"disproven"})

    assert commit == "a" * 40
    assert child_round is None
