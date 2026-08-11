"""Round transactions against real temporary project repositories."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from vibesys.run import (
    GitTracker,
    RoundRecoveryOutcome,
    RoundTransactionCoordinator,
    RoundTransactionError,
)
from vibesys.run.git_tracker import GitTrackingMode
from vs_loop_state import RoundRecord, serialize_round_record
from vs_project_state import ProjectStore, RunConfiguration

if TYPE_CHECKING:
    from pathlib import Path

_RUN_ID = "transaction-test"


def _configuration() -> RunConfiguration:
    return RunConfiguration(
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
        mode=GitTrackingMode.USER_PROJECT,
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
    metadata_paths = (
        store.metadata_gitignore_path,
        store.project_manifest_path,
        store.run_manifest_path(_RUN_ID),
    )
    tracker.snapshot_with_framework_metadata(
        "initialize run",
        {path.relative_to(tmp_path): path.read_bytes() for path in metadata_paths},
    )
    return store, tracker, RoundTransactionCoordinator(store, tracker, _RUN_ID)


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


def _local_round_payload(record: RoundRecord) -> str:
    return json.dumps([serialize_round_record(record)], indent=2) + "\n"


def test_complete_atomically_commits_candidate_and_exact_round_metadata(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    active_path = store.active_state_path(_RUN_ID)
    active_path.write_bytes(b'{"hypothesis":"before"}\n')
    record = _record(tracker)
    transitioned = b'{"hypothesis":"after"}\n'

    transaction = coordinator.begin(record, next_active_contents=transitioned)
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    active_path.write_bytes(transitioned)
    completed = transaction.complete()

    assert completed.metadata_path == store.rounds_dir(_RUN_ID) / "0001.json"
    assert completed.checkpoint == tracker.current_sha()
    assert _git_stdout(tracker, "show", "HEAD:main.py") == b"VALUE = 2\n"
    assert _git_stdout(tracker, "show", "HEAD:.vs/runs/transaction-test/rounds/0001.json") == (
        completed.metadata_path.read_bytes()
    )
    assert active_path.read_bytes() == transitioned
    assert not coordinator.journal_path.exists()
    assert _git_stdout(tracker, "ls-files", ".vs/local") == b""
    assert coordinator.recover() is RoundRecoveryOutcome.NO_TRANSACTION


def test_recovery_before_portable_persistence_rolls_completed_round_forward(
    tmp_path: Path,
) -> None:
    store, tracker, coordinator = _project(tmp_path)
    active_path = store.active_state_path(_RUN_ID)
    active_path.write_text("before\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    artifact = tmp_path / "attempts" / "round-1-attempt-1.json"
    artifact.parent.mkdir()
    artifact.write_text('{"outcome":"passed"}\n', encoding="utf-8")
    record = _record(tracker)

    coordinator.begin(record, next_active_contents=None)

    restarted = RoundTransactionCoordinator(store, tracker, _RUN_ID)
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


def test_recovery_after_local_completed_state_preserves_paid_attempt(
    tmp_path: Path,
) -> None:
    store, tracker, coordinator = _project(tmp_path)
    active_path = store.active_state_path(_RUN_ID)
    active_path.write_text("before\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    artifact = tmp_path / "attempts" / "round-1-attempt-1.json"
    artifact.parent.mkdir()
    artifact.write_text('{"outcome":"passed"}\n', encoding="utf-8")
    record = _record(tracker)
    transitioned = b"next hypothesis\n"
    coordinator.begin(record, next_active_contents=transitioned)

    active_path.write_bytes(transitioned)
    local_rounds = store.logs_dir(_RUN_ID) / "rounds.json"
    local_rounds.parent.mkdir(parents=True, exist_ok=True)
    local_rounds.write_text(_local_round_payload(record), encoding="utf-8")

    restarted = RoundTransactionCoordinator(store, tracker, _RUN_ID)
    assert restarted.recover() is RoundRecoveryOutcome.COMMITTED

    assert store.load_rounds(_RUN_ID) == [record]
    assert active_path.read_bytes() == transitioned
    assert local_rounds.read_text(encoding="utf-8") == _local_round_payload(record)
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
    active_path = store.active_state_path(_RUN_ID)
    active_path.write_text("before\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    record = _record(tracker)
    transaction = coordinator.begin(record, next_active_contents=b"after\n")
    active_path.write_text("after\n", encoding="utf-8")
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
    assert active_path.read_text(encoding="utf-8") == "after\n"
    assert store.load_rounds(_RUN_ID) == [record]
    assert _git_stdout(tracker, "show", "HEAD:main.py") == b"VALUE = 2\n"
    assert _git_stdout(tracker, "diff", "--cached", "--name-only") == b""


def test_recovery_after_commit_retains_active_and_restores_committed_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, tracker, coordinator = _project(tmp_path)
    active_path = store.active_state_path(_RUN_ID)
    active_path.write_text("before\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    record = _record(tracker)
    transaction = coordinator.begin(record, next_active_contents=b"after\n")
    active_path.write_text("after\n", encoding="utf-8")

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
        "HEAD:.vs/runs/transaction-test/rounds/0001.json",
    )
    round_path.write_text("forged after commit\n", encoding="utf-8")
    active_path.write_text("forged active\n", encoding="utf-8")

    restarted = RoundTransactionCoordinator(store, tracker, _RUN_ID)
    assert restarted.recover() is RoundRecoveryOutcome.COMMITTED
    assert active_path.read_text(encoding="utf-8") == "after\n"
    assert round_path.read_bytes() == committed
    assert not restarted.journal_path.exists()
    assert tracker.current_sha() == committed_head
    assert restarted.recover() is RoundRecoveryOutcome.NO_TRANSACTION


def test_begin_rejects_staged_changes_without_creating_a_journal(tmp_path: Path) -> None:
    _store, tracker, coordinator = _project(tmp_path)
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    tracker.run(["git", "add", "--", "main.py"])

    with pytest.raises(RoundTransactionError, match="index contains staged changes"):
        coordinator.begin(_record(tracker), next_active_contents=None)

    assert not coordinator.journal_path.exists()


def test_second_complete_is_rejected(tmp_path: Path) -> None:
    _store, tracker, coordinator = _project(tmp_path)
    transaction = coordinator.begin(_record(tracker), next_active_contents=None)
    transaction.complete()

    with pytest.raises(RoundTransactionError, match="already completed"):
        transaction.complete()


def test_recovery_rejects_tampered_round_payload_without_mutating_state(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    active_path = store.active_state_path(_RUN_ID)
    active_path.write_text("before\n", encoding="utf-8")
    coordinator.begin(_record(tracker), next_active_contents=b"after\n")
    payload = json.loads(coordinator.journal_path.read_text(encoding="utf-8"))
    payload["round_payload_sha256"] = "0" * 64
    coordinator.journal_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoundTransactionError, match="payload digest does not match"):
        coordinator.recover()

    assert active_path.read_text(encoding="utf-8") == "before\n"
    assert coordinator.journal_path.exists()
    assert store.load_rounds(_RUN_ID) == []


def test_recovery_rejects_unknown_journal_fields_without_mutating_state(tmp_path: Path) -> None:
    store, tracker, coordinator = _project(tmp_path)
    active_path = store.active_state_path(_RUN_ID)
    active_path.write_text("before\n", encoding="utf-8")
    coordinator.begin(_record(tracker), next_active_contents=b"after\n")
    payload = json.loads(coordinator.journal_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    coordinator.journal_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoundTransactionError, match="unexpected"):
        coordinator.recover()

    assert active_path.read_text(encoding="utf-8") == "before\n"
    assert coordinator.journal_path.exists()
    assert store.load_rounds(_RUN_ID) == []


def test_coordinator_requires_matching_project_mode_tracker(tmp_path: Path) -> None:
    store, tracker, _coordinator = _project(tmp_path)
    wrong_run = GitTracker(
        tmp_path,
        log=lambda _message: None,
        mode=GitTrackingMode.USER_PROJECT,
        run_id="another-run",
    )
    with pytest.raises(RoundTransactionError, match="does not match"):
        RoundTransactionCoordinator(store, wrong_run, _RUN_ID)

    legacy = GitTracker(tmp_path, log=lambda _message: None)
    with pytest.raises(RoundTransactionError, match="user-project mode"):
        RoundTransactionCoordinator(store, legacy, _RUN_ID)

    assert tracker.current_sha() is not None
