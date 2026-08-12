"""Round transactions against real temporary project repositories."""

from __future__ import annotations

import base64
import json
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
from vs_project_state import AgentRunConfiguration, ProjectStore, StateTransition

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


def _project(tmp_path: Path) -> tuple[ProjectStore, GitTracker, RoundTransactionCoordinator]:
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    store = ProjectStore(tmp_path)
    store.create_project("transaction test", now=datetime(2026, 8, 11, tzinfo=UTC))

    tracker = GitTracker(
        tmp_path,
        log=lambda _message: None,
        run_id=_RUN_ID,
    )
    tracker.init(existing=False)
    trusted_input_baseline = tracker.trusted_input_baseline
    assert trusted_input_baseline is not None
    branch = tracker.project_branch
    assert branch is not None

    manifest = store.new_run_manifest(
        "transaction test",
        run_id=_RUN_ID,
        branch=branch,
        vibesys_version="0.1.0",
        configuration=_configuration(),
        trusted_input_baseline=trusted_input_baseline,
        now=datetime(2026, 8, 11, 0, 1, tzinfo=UTC),
    )
    store.create_run(manifest)
    tracker.snapshot_with_framework_metadata(
        "initialize run",
        store.initialization_snapshot(_RUN_ID),
    )
    return (
        store,
        tracker,
        RoundTransactionCoordinator(store, tracker, _RUN_ID, active_state_model_type=_ActiveState),
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


def _git_stdout(tracker: GitTracker, *args: str) -> bytes:
    return tracker.run(["git", *args]).stdout


def _active_transition(store: ProjectStore, hypothesis: str | None) -> StateTransition:
    model = None if hypothesis is None else _ActiveState(hypothesis=hypothesis)
    return store.local_namespace(_RUN_ID, "agent").transition("active.json", model)


def _apply_active(store: ProjectStore, transition: StateTransition) -> None:
    store.local_namespace(_RUN_ID, "agent").apply(transition)


def _save_active(store: ProjectStore, hypothesis: str) -> None:
    store.local_namespace(_RUN_ID, "agent").save("active.json", _ActiveState(hypothesis=hypothesis))


def _load_active(store: ProjectStore) -> _ActiveState | None:
    return store.local_namespace(_RUN_ID, "agent").load_optional("active.json", _ActiveState)


def test_complete_atomically_commits_candidate_and_exact_round_metadata(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    active_path = tmp_path / ".vs/local/runs" / _RUN_ID / "agent/active.json"
    _save_active(store, "before")
    record = _record(tracker)
    transition = _active_transition(store, "after")

    transaction = coordinator.begin(record, active_transition=transition)
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    _apply_active(store, transition)
    completed = transaction.complete()

    assert completed.metadata_path == store.rounds_dir(_RUN_ID) / "0001.json"
    assert completed.checkpoint == tracker.current_sha()
    assert _git_stdout(tracker, "show", "HEAD:main.py") == b"VALUE = 2\n"
    assert _git_stdout(
        tracker,
        "show",
        f"HEAD:{completed.metadata_path.relative_to(tmp_path)}",
    ) == (completed.metadata_path.read_bytes())
    assert transition.next_document is not None
    assert active_path.read_bytes() == transition.next_document.contents
    assert _load_active(store) == _ActiveState(hypothesis="after")
    assert not coordinator.journal_path.exists()
    assert _git_stdout(tracker, "ls-files", ".vs/local") == b""
    assert coordinator.recover() is RoundRecoveryOutcome.NO_TRANSACTION


def test_recovery_before_portable_persistence_rolls_completed_round_forward(
    tmp_path: Path,
) -> None:
    store, tracker, coordinator = _project(tmp_path)
    active_path = tmp_path / ".vs/local/runs" / _RUN_ID / "agent/active.json"
    _save_active(store, "before")
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    artifact = tmp_path / "attempts" / "round-1-attempt-1.json"
    artifact.parent.mkdir()
    artifact.write_text('{"outcome":"passed"}\n', encoding="utf-8")
    record = _record(tracker)

    coordinator.begin(record, active_transition=_active_transition(store, None))

    restarted = RoundTransactionCoordinator(
        store, tracker, _RUN_ID, active_state_model_type=_ActiveState
    )
    assert restarted.recover() is RoundRecoveryOutcome.COMMITTED

    round_path = store.rounds_dir(_RUN_ID) / "0001.json"
    assert store.load_rounds(_RUN_ID) == [record]
    assert _git_stdout(tracker, "show", "HEAD:main.py") == b"VALUE = 2\n"
    assert _git_stdout(tracker, "show", f"HEAD:{artifact.relative_to(tmp_path)}") == (
        artifact.read_bytes()
    )
    assert _git_stdout(tracker, "show", f"HEAD:{round_path.relative_to(tmp_path)}") == (
        round_path.read_bytes()
    )
    assert not active_path.exists()
    assert restarted.recover() is RoundRecoveryOutcome.NO_TRANSACTION


def test_recovery_after_active_state_update_preserves_paid_attempt(
    tmp_path: Path,
) -> None:
    store, tracker, coordinator = _project(tmp_path)
    _save_active(store, "before")
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    artifact = tmp_path / "attempts" / "round-1-attempt-1.json"
    artifact.parent.mkdir()
    artifact.write_text('{"outcome":"passed"}\n', encoding="utf-8")
    record = _record(tracker)
    transition = _active_transition(store, "next hypothesis")
    coordinator.begin(record, active_transition=transition)

    _apply_active(store, transition)

    restarted = RoundTransactionCoordinator(
        store, tracker, _RUN_ID, active_state_model_type=_ActiveState
    )
    assert restarted.recover() is RoundRecoveryOutcome.COMMITTED

    assert store.load_rounds(_RUN_ID) == [record]
    assert _load_active(store) == _ActiveState(hypothesis="next hypothesis")
    assert _git_stdout(tracker, "show", "HEAD:main.py") == b"VALUE = 2\n"
    assert _git_stdout(tracker, "show", f"HEAD:{artifact.relative_to(tmp_path)}") == (
        artifact.read_bytes()
    )
    assert _git_stdout(tracker, "ls-files", ".vs/local") == b""


def test_snapshot_failure_leaves_a_roll_forward_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, tracker, coordinator = _project(tmp_path)
    _save_active(store, "before")
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    record = _record(tracker)
    transition = _active_transition(store, "after")
    transaction = coordinator.begin(record, active_transition=transition)
    _apply_active(store, transition)
    original_snapshot = tracker.snapshot_with_framework_metadata

    def fail_snapshot(_label: str, _metadata: object) -> None:
        tracker.run(["git", "add", "--", "main.py"])
        raise RuntimeError("simulated process failure before commit")  # noqa: TRY003

    monkeypatch.setattr(tracker, "snapshot_with_framework_metadata", fail_snapshot)
    with pytest.raises(RuntimeError, match="simulated process failure"):
        transaction.complete()

    assert coordinator.journal_path.exists()
    assert store.rounds_dir(_RUN_ID).joinpath("0001.json").exists()
    monkeypatch.setattr(tracker, "snapshot_with_framework_metadata", original_snapshot)

    assert coordinator.recover() is RoundRecoveryOutcome.COMMITTED
    assert _load_active(store) == _ActiveState(hypothesis="after")
    assert store.load_rounds(_RUN_ID) == [record]
    assert _git_stdout(tracker, "show", "HEAD:main.py") == b"VALUE = 2\n"
    assert _git_stdout(tracker, "diff", "--cached", "--name-only") == b""


def test_recovery_after_commit_retains_active_and_restores_committed_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, tracker, coordinator = _project(tmp_path)
    active_path = tmp_path / ".vs/local/runs" / _RUN_ID / "agent/active.json"
    _save_active(store, "before")
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
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
    round_path = store.rounds_dir(_RUN_ID) / "0001.json"
    committed = _git_stdout(
        tracker,
        "show",
        f"HEAD:{store.rounds_dir(_RUN_ID).relative_to(tmp_path)}/0001.json",
    )
    round_path.write_text("forged after commit\n", encoding="utf-8")
    active_path.write_text("forged active\n", encoding="utf-8")

    restarted = RoundTransactionCoordinator(
        store, tracker, _RUN_ID, active_state_model_type=_ActiveState
    )
    assert restarted.recover() is RoundRecoveryOutcome.COMMITTED
    assert _load_active(store) == _ActiveState(hypothesis="after")
    assert round_path.read_bytes() == committed
    assert not restarted.journal_path.exists()
    assert tracker.current_sha() == committed_head
    assert restarted.recover() is RoundRecoveryOutcome.NO_TRANSACTION


def test_begin_rejects_staged_changes_without_creating_a_journal(tmp_path: Path) -> None:
    _store, tracker, coordinator = _project(tmp_path)
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    tracker.run(["git", "add", "--", "main.py"])

    with pytest.raises(RoundTransactionError, match="index contains staged changes"):
        coordinator.begin(_record(tracker), active_transition=_active_transition(_store, None))

    assert not coordinator.journal_path.exists()


def test_begin_rejects_transition_outside_agent_active_state(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    wrong_transition = store.local_namespace(_RUN_ID, "plain").transition(
        "cursor.json", _ActiveState(hypothesis="after")
    )

    with pytest.raises(RoundTransactionError, match="must target"):
        coordinator.begin(_record(tracker), active_transition=wrong_transition)

    assert not coordinator.journal_path.exists()


def test_second_complete_is_rejected(tmp_path: Path) -> None:
    _store, tracker, coordinator = _project(tmp_path)
    transaction = coordinator.begin(
        _record(tracker), active_transition=_active_transition(_store, None)
    )
    transaction.complete()

    with pytest.raises(RoundTransactionError, match="already completed"):
        transaction.complete()


def test_recovery_rejects_tampered_round_payload_without_mutating_state(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    _save_active(store, "before")
    coordinator.begin(_record(tracker), active_transition=_active_transition(store, "after"))
    payload = json.loads(coordinator.journal_path.read_text(encoding="utf-8"))
    payload["round_payload_sha256"] = "0" * 64
    coordinator.journal_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoundTransactionError, match="payload digest does not match"):
        coordinator.recover()

    assert _load_active(store) == _ActiveState(hypothesis="before")
    assert coordinator.journal_path.exists()
    assert store.load_rounds(_RUN_ID) == []


def test_recovery_rejects_unknown_journal_fields_without_mutating_state(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    _save_active(store, "before")
    coordinator.begin(_record(tracker), active_transition=_active_transition(store, "after"))
    payload = json.loads(coordinator.journal_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    coordinator.journal_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoundTransactionError, match="unexpected"):
        coordinator.recover()

    assert _load_active(store) == _ActiveState(hypothesis="before")
    assert coordinator.journal_path.exists()
    assert store.load_rounds(_RUN_ID) == []


def test_recovery_rejects_tampered_active_transition_path(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    _save_active(store, "before")
    coordinator.begin(_record(tracker), active_transition=_active_transition(store, "after"))
    payload = json.loads(coordinator.journal_path.read_text(encoding="utf-8"))
    payload["active_state_path"] = ".vs/local/runs/transaction-test/plain/cursor.json"
    coordinator.journal_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoundTransactionError, match="Invalid active-state transition"):
        coordinator.recover()

    assert _load_active(store) == _ActiveState(hypothesis="before")
    assert coordinator.journal_path.exists()
    assert store.load_rounds(_RUN_ID) == []


def test_recovery_rejects_schema_invalid_active_state_document(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    _save_active(store, "before")
    coordinator.begin(_record(tracker), active_transition=_active_transition(store, "after"))
    payload = json.loads(coordinator.journal_path.read_text(encoding="utf-8"))
    payload["active_state_document_base64"] = base64.b64encode(b'{"unexpected":true}').decode(
        "ascii"
    )
    coordinator.journal_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoundTransactionError, match="typed slot schema"):
        coordinator.recover()

    assert _load_active(store) == _ActiveState(hypothesis="before")
    assert coordinator.journal_path.exists()
    assert store.load_rounds(_RUN_ID) == []


def test_coordinator_requires_matching_run_tracker(tmp_path: Path) -> None:
    store, tracker, _coordinator = _project(tmp_path)
    wrong_run = GitTracker(
        tmp_path,
        log=lambda _message: None,
        run_id="another-run",
    )
    with pytest.raises(RoundTransactionError, match="does not match"):
        RoundTransactionCoordinator(store, wrong_run, _RUN_ID, active_state_model_type=_ActiveState)

    assert tracker.current_sha() is not None
