"""Round transactions composed from typed state and real Git repositories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, ConfigDict

from vibesys.run import (
    GitTracker,
    RoundRecoveryOutcome,
    RoundTransactionCoordinator,
    RoundTransactionError,
)
from vs_loop_state import RoundRecord
from vs_project import AgentRunConfiguration, Project, StateTransition

if TYPE_CHECKING:
    from pathlib import Path

_RUN_ID = "transaction-test"


class _ActiveState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    hypothesis: str


def _configuration() -> AgentRunConfiguration:
    return AgentRunConfiguration(
        outer_loop="agent",
        inner_loop="multi-agent",
        interface="inprocess",
        agent_backend="cli",
        compute_backend="cpu",
        max_rounds=5,
        max_retries_per_round=2,
        judge_every=1,
        official_eval_every=1,
        memory_layout="files",
    )


def _project(tmp_path: Path) -> tuple[Project, GitTracker, RoundTransactionCoordinator]:
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    project = Project.open(tmp_path)
    project.state.create_project("transaction test", now=datetime(2026, 8, 11, tzinfo=UTC))
    tracker = GitTracker(tmp_path, log=lambda _message: None, run_id=_RUN_ID)
    tracker.init(existing=False)
    assert tracker.trusted_input_baseline is not None
    assert tracker.project_branch is not None
    manifest = project.state.new_run_manifest(
        "transaction test",
        run_id=_RUN_ID,
        branch=tracker.project_branch,
        vibesys_version="0.1.0",
        configuration=_configuration(),
        trusted_input_baseline=tracker.trusted_input_baseline,
        now=datetime(2026, 8, 11, 0, 1, tzinfo=UTC),
    )
    project.state.create_run(manifest)
    tracker.snapshot_with_framework_metadata(
        "initialize run",
        project.state.initialization_snapshot(_RUN_ID),
    )
    return (
        project,
        tracker,
        RoundTransactionCoordinator(
            project, tracker, _RUN_ID, active_state_model_type=_ActiveState
        ),
    )


def _record(tracker: GitTracker, round_number: int = 1) -> RoundRecord:
    return RoundRecord(
        round_number=round_number,
        commit=tracker.current_sha(),
        perf_metric=12.5,
        perf_unit="ns/op",
        passed=True,
        hypothesis_id=f"hypothesis-{round_number}",
        hypothesis_outcome="proven",
    )


def _active_transition(project: Project, hypothesis: str | None) -> StateTransition:
    model = None if hypothesis is None else _ActiveState(hypothesis=hypothesis)
    return project.state.local_namespace(_RUN_ID, "agent").transition("active.json", model)


def _apply_active(project: Project, transition: StateTransition) -> None:
    project.state.local_namespace(_RUN_ID, "agent").apply(transition)


def _save_active(project: Project, hypothesis: str) -> None:
    project.state.local_namespace(_RUN_ID, "agent").save(
        "active.json",
        _ActiveState(hypothesis=hypothesis),
    )


def _load_active(project: Project) -> _ActiveState | None:
    return project.state.local_namespace(_RUN_ID, "agent").load_optional(
        "active.json",
        _ActiveState,
    )


def test_complete_commits_candidate_and_typed_round_state(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    _save_active(store, "before")
    record = _record(tracker)
    transition = _active_transition(store, "after")

    transaction = coordinator.begin(record, active_transition=transition)
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    _apply_active(store, transition)
    completed = transaction.complete()

    assert completed.checkpoint == tracker.current_sha()
    assert tracker.run(["git", "show", "HEAD:main.py"]).stdout == b"VALUE = 2\n"
    assert store.state.load_rounds(_RUN_ID) == [record]
    assert _load_active(store) == _ActiveState(hypothesis="after")
    assert coordinator.recover() is RoundRecoveryOutcome.NO_TRANSACTION


def test_recovery_rolls_prepared_round_forward(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    _save_active(store, "before")
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    record = _record(tracker)
    coordinator.begin(record, active_transition=_active_transition(store, None))

    restarted = RoundTransactionCoordinator(
        store,
        tracker,
        _RUN_ID,
        active_state_model_type=_ActiveState,
    )

    assert restarted.recover() is RoundRecoveryOutcome.COMMITTED
    assert store.state.load_rounds(_RUN_ID) == [record]
    assert _load_active(store) is None
    assert tracker.run(["git", "show", "HEAD:main.py"]).stdout == b"VALUE = 2\n"
    assert restarted.recover() is RoundRecoveryOutcome.NO_TRANSACTION


def test_recovery_preserves_an_already_applied_active_transition(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    record = _record(tracker)
    transition = _active_transition(store, "next hypothesis")
    coordinator.begin(record, active_transition=transition)
    _apply_active(store, transition)

    restarted = RoundTransactionCoordinator(
        store,
        tracker,
        _RUN_ID,
        active_state_model_type=_ActiveState,
    )

    assert restarted.recover() is RoundRecoveryOutcome.COMMITTED
    assert _load_active(store) == _ActiveState(hypothesis="next hypothesis")
    assert store.state.load_rounds(_RUN_ID) == [record]


def test_recovery_translates_corrupt_journal_state(tmp_path: Path) -> None:
    store, _tracker, coordinator = _project(tmp_path)
    journal_directory = store.state.local_namespace(_RUN_ID, "transaction").external_directory()
    (journal_directory / "round.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(RoundTransactionError, match="Invalid round transaction journal"):
        coordinator.recover()


def test_begin_translates_corrupt_journal_state(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    journal_directory = store.state.local_namespace(_RUN_ID, "transaction").external_directory()
    (journal_directory / "round.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(RoundTransactionError, match="Invalid round transaction journal"):
        coordinator.begin(_record(tracker), active_transition=_active_transition(store, None))


def test_snapshot_failure_remains_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, tracker, coordinator = _project(tmp_path)
    record = _record(tracker)
    transition = _active_transition(store, "after")
    transaction = coordinator.begin(record, active_transition=transition)
    _apply_active(store, transition)
    original_snapshot = tracker.snapshot_with_framework_metadata

    def fail_snapshot(_label: str, _snapshot: object) -> None:
        raise RuntimeError("simulated process failure")  # noqa: TRY003

    monkeypatch.setattr(tracker, "snapshot_with_framework_metadata", fail_snapshot)
    with pytest.raises(RuntimeError, match="simulated process failure"):
        transaction.complete()
    monkeypatch.setattr(tracker, "snapshot_with_framework_metadata", original_snapshot)

    assert coordinator.recover() is RoundRecoveryOutcome.COMMITTED
    assert store.state.load_rounds(_RUN_ID) == [record]
    assert _load_active(store) == _ActiveState(hypothesis="after")


def test_recovery_after_commit_does_not_create_a_duplicate_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, tracker, coordinator = _project(tmp_path)
    record = _record(tracker)
    transition = _active_transition(store, "after")
    transaction = coordinator.begin(record, active_transition=transition)
    _apply_active(store, transition)

    def fail_to_clear() -> None:
        raise OSError("simulated process failure after commit")  # noqa: TRY003

    monkeypatch.setattr(coordinator, "_clear_journal", fail_to_clear)
    with pytest.raises(OSError, match="simulated process failure"):
        transaction.complete()
    committed_head = tracker.current_sha()
    completed_round_directory = store.state.portable_namespace(
        _RUN_ID,
        "agent",
    ).external_directory("rounds")
    (completed_round_directory / "0001.json").write_text("{not-json", encoding="utf-8")

    restarted = RoundTransactionCoordinator(
        store,
        tracker,
        _RUN_ID,
        active_state_model_type=_ActiveState,
    )
    assert restarted.recover() is RoundRecoveryOutcome.COMMITTED
    assert tracker.current_sha() == committed_head
    assert store.state.load_rounds(_RUN_ID) == [record]


def test_begin_rejects_staged_changes_without_leaving_a_transaction(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    tracker.run(["git", "add", "--", "main.py"])

    with pytest.raises(RoundTransactionError, match="index contains staged changes"):
        coordinator.begin(_record(tracker), active_transition=_active_transition(store, None))

    tracker.run(["git", "reset", "--quiet", "HEAD", "--", "."])
    assert coordinator.recover() is RoundRecoveryOutcome.NO_TRANSACTION


def test_begin_rejects_a_transition_from_another_namespace(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    wrong_transition = store.state.local_namespace(_RUN_ID, "plain").transition(
        "cursor.json",
        _ActiveState(hypothesis="after"),
    )

    with pytest.raises(RoundTransactionError, match="typed slot"):
        coordinator.begin(_record(tracker), active_transition=wrong_transition)

    assert coordinator.recover() is RoundRecoveryOutcome.NO_TRANSACTION


def test_transaction_handle_cannot_complete_twice(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    transaction = coordinator.begin(
        _record(tracker),
        active_transition=_active_transition(store, None),
    )
    transaction.complete()

    with pytest.raises(RoundTransactionError, match="already completed"):
        transaction.complete()


def test_coordinator_requires_matching_run_tracker(tmp_path: Path) -> None:
    store, tracker, _coordinator = _project(tmp_path)
    wrong_run = GitTracker(tmp_path, log=lambda _message: None, run_id="another-run")

    with pytest.raises(RoundTransactionError, match="does not match"):
        RoundTransactionCoordinator(
            store,
            wrong_run,
            _RUN_ID,
            active_state_model_type=_ActiveState,
        )

    assert tracker.current_sha() is not None
