"""Recoverable application transaction for one completed optimization round.

``Project.state`` owns the portable state filesystem contract and
``GitTracker`` owns project history. This module composes them at the
application boundary where one completed round must update both stores while
also advancing machine-local active-hypothesis state.

Callers prepare the transaction with the completed ``RoundRecord`` and a typed
active-state transition before mutating either local state file. A machine-local
journal makes every intermediate state recoverable after a process crash.
Recovery always rolls the completed round forward, preserving paid agent work
and its candidate tree.
"""

# These boundary errors deliberately name the relevant path or transaction.
# ruff: noqa: TRY003

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from vibesys.run.git_tracker import FrameworkSnapshotStatus
from vs_loop_state import RoundRecord, parse_round_record
from vs_project import (
    ProjectStateError,
    StateSlot,
    StateTransition,
    serialize_round,
)

if TYPE_CHECKING:
    from vibesys.run.git_tracker import GitTracker
    from vs_project import Project

_JOURNAL_SCHEMA_VERSION: Literal[3] = 3
_GIT_OBJECT_ID_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class RoundTransactionError(RuntimeError):
    """Raised when a completed-round transaction cannot proceed safely."""


class RoundRecoveryOutcome(StrEnum):
    """Observable result of recovering the current run's transaction."""

    NO_TRANSACTION = "no-transaction"
    COMMITTED = "committed"


class _RoundJournal(BaseModel):
    """Strict machine-local write-ahead record for one completed round."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[3]
    run_id: str
    round_number: Annotated[int, Field(gt=0)]
    pre_commit: Annotated[str, Field(pattern=_GIT_OBJECT_ID_PATTERN)]
    active_transition_base64: str
    round_payload_base64: str
    round_payload_sha256: Annotated[str, Field(pattern=_SHA256_PATTERN)]

    @field_validator("active_transition_base64", "round_payload_base64")
    @classmethod
    def _validate_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("must contain canonical base64-encoded bytes") from exc
        return value

    def active_transition(self, slot: StateSlot[BaseModel]) -> StateTransition:
        """Decode the completed round's transition through its typed slot."""
        return slot.deserialize_transition(
            base64.b64decode(self.active_transition_base64, validate=True)
        )

    def round_payload(self) -> bytes:
        """Return the exact portable completed-round payload."""
        return base64.b64decode(self.round_payload_base64, validate=True)


@dataclass(frozen=True)
class CompletedRound:
    """Durable outputs produced by a successful round transaction."""

    checkpoint: str


class RoundTransaction:
    """A prepared round transition obtained from ``coordinator.begin``."""

    def __init__(self, coordinator: RoundTransactionCoordinator, round_number: int) -> None:
        """Bind this handle to one coordinator and round number."""
        self._coordinator = coordinator
        self.round_number = round_number
        self._closed = False

    def complete(self) -> CompletedRound:
        """Snapshot the prepared round payload and finish the transaction."""
        if self._closed:
            raise RoundTransactionError(
                f"Round {self.round_number} transaction has already completed"
            )
        result = self._coordinator._complete(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            self.round_number
        )
        self._closed = True
        return result


class RoundTransactionCoordinator:
    """Coordinate recoverable completed-round writes across local state and Git.

    The coordinator is intentionally application-specific. It composes the
    portable project-state library with project-mode Git tracking and exposes
    only three operations:

    1. ``begin(record, active_transition)`` journals the exact portable round
       payload and desired typed active-state transition.
    2. The caller updates its machine-local active state.
    3. ``RoundTransaction.complete()`` writes and commits the prepared round.

    ``recover()`` is idempotent. It commits the exact journaled payload when
    needed, restores the desired active state, and then clears the journal.
    Because ``begin`` accepts only an already completed record, recovery never
    discards the result or causes its paid attempt to be replayed.
    """

    def __init__(
        self,
        project: Project,
        git: GitTracker,
        run_id: str,
        *,
        active_state_model_type: type[BaseModel],
    ) -> None:
        """Validate and bind the project, Git tracker, and run identity."""
        project_root = project.root.resolve()
        if git.root.resolve() != project_root:
            raise RoundTransactionError(
                "Round transaction project and Git tracker must use the same project root"
            )
        if git.run_id != run_id:
            raise RoundTransactionError(
                f"Round transaction run {run_id!r} does not match Git tracker run {git.run_id!r}"
            )

        project.state.load_run(run_id)
        self._project = project
        self._git = git
        self.run_id = run_id
        self._active_state_slot: StateSlot[BaseModel] = project.state.local_namespace(
            run_id,
            "agent",
        ).slot("active.json", active_state_model_type)
        self._journal_slot = project.state.local_namespace(run_id, "transaction").slot(
            "round.json",
            _RoundJournal,
        )

    def begin(
        self,
        record: RoundRecord,
        *,
        active_transition: StateTransition,
    ) -> RoundTransaction:
        """Durably prepare a completed round before mutating local state."""
        round_number = record.round_number
        if round_number < 1:
            raise RoundTransactionError(f"Round number must be positive, got {round_number}")
        if self._load_optional_journal() is not None:
            raise RoundTransactionError(
                "An unfinished round transaction already exists; recover it before "
                "beginning another"
            )

        completed_rounds = self._project.state.load_rounds(self.run_id)
        if round_number <= len(completed_rounds):
            raise RoundTransactionError(
                f"Completed round {round_number} already exists for run {self.run_id!r}"
            )
        expected_snapshot = self._project.state.prepare_completed_round_snapshot(
            self.run_id, record
        )
        if (
            self._git.framework_snapshot_status(expected_snapshot)
            is not FrameworkSnapshotStatus.MISSING
        ):
            raise RoundTransactionError(
                f"Completed round {round_number} already exists in project history"
            )

        pre_commit = self._git.current_sha()
        if pre_commit is None:
            raise RoundTransactionError("Round transactions require an initialized Git HEAD")
        self._require_clean_index()
        self._validate_active_transition(active_transition)

        round_payload = serialize_round(record)
        journal = _RoundJournal(
            schema_version=_JOURNAL_SCHEMA_VERSION,
            run_id=self.run_id,
            round_number=round_number,
            pre_commit=pre_commit,
            active_transition_base64=base64.b64encode(
                self._active_state_slot.serialize_transition(active_transition)
            ).decode("ascii"),
            round_payload_base64=base64.b64encode(round_payload).decode("ascii"),
            round_payload_sha256=_sha256(round_payload),
        )
        self._journal_slot.save(journal)
        return RoundTransaction(self, round_number)

    def recover(self) -> RoundRecoveryOutcome:
        """Commit any journaled completed round and restore its local state."""
        journal = self._load_optional_journal()
        if journal is None:
            return RoundRecoveryOutcome.NO_TRANSACTION

        if not self._pre_commit_is_ancestor(journal.pre_commit):
            raise RoundTransactionError(
                "Cannot recover round transaction after Git history moved away from "
                f"its starting commit {journal.pre_commit}"
            )
        self._commit_prepared_round(journal)
        self._restore_active(journal.active_transition(self._active_state_slot))
        self._clear_journal()
        return RoundRecoveryOutcome.COMMITTED

    def _complete(self, round_number: int) -> CompletedRound:
        journal = self._load_journal()
        if journal.round_number != round_number:
            raise RoundTransactionError(
                f"Journal is for round {journal.round_number}, not round {round_number}"
            )

        completed = self._commit_prepared_round(journal)
        self._restore_active(journal.active_transition(self._active_state_slot))
        self._clear_journal()
        return completed

    def _commit_prepared_round(self, journal: _RoundJournal) -> CompletedRound:
        round_payload = journal.round_payload()
        record = _parse_round_payload(round_payload, source="round transaction journal")
        if record.round_number != journal.round_number:
            raise RoundTransactionError(
                f"Round transaction journal payload is for round {record.round_number}, "
                f"not round {journal.round_number}"
            )
        expected_snapshot = self._project.state.prepare_completed_round_snapshot(
            self.run_id, record
        )
        status = self._git.framework_snapshot_status(expected_snapshot)
        if status is FrameworkSnapshotStatus.DIFFERENT:
            raise RoundTransactionError(
                "Committed round metadata differs from the transaction journal"
            )
        if status is FrameworkSnapshotStatus.EXACT:
            snapshot = self._project.state.restore_completed_round(self.run_id, record)
        else:
            snapshot = self._project.state.save_round(self.run_id, record)
            self._git.snapshot_with_framework_metadata(
                f"vibesys(round {journal.round_number}): record result",
                snapshot,
            )
        if self._git.framework_snapshot_status(snapshot) is not FrameworkSnapshotStatus.EXACT:
            raise RoundTransactionError(
                "Git snapshot did not commit the exact completed-round metadata"
            )
        checkpoint = self._git.current_sha()
        if checkpoint is None:
            raise RoundTransactionError("Git snapshot completed without an accessible HEAD")
        return CompletedRound(checkpoint=checkpoint)

    def _load_journal(self) -> _RoundJournal:
        journal = self._load_optional_journal()
        if journal is None:
            raise RoundTransactionError("Round transaction journal does not exist")
        return journal

    def _load_optional_journal(self) -> _RoundJournal | None:
        """Load and validate the journal while preserving the coordinator error API."""
        try:
            journal = self._journal_slot.load_optional()
        except ProjectStateError as exc:
            raise RoundTransactionError(f"Invalid round transaction journal: {exc}") from exc
        if journal is None:
            return None
        if journal.run_id != self.run_id:
            raise RoundTransactionError(
                f"Round transaction journal belongs to run {journal.run_id!r}, not {self.run_id!r}"
            )
        if _sha256(journal.round_payload()) != journal.round_payload_sha256:
            raise RoundTransactionError("Round transaction journal payload digest does not match")
        _parse_round_payload(journal.round_payload(), source="round transaction journal")
        try:
            self._validate_active_transition(journal.active_transition(self._active_state_slot))
        except (TypeError, ValueError, RoundTransactionError) as exc:
            raise RoundTransactionError(
                f"Invalid active-state transition in round transaction journal: {exc}"
            ) from exc
        return journal

    def _pre_commit_is_ancestor(self, pre_commit: str) -> bool:
        result = self._git.run(
            ["git", "merge-base", "--is-ancestor", pre_commit, "HEAD"],
            check=False,
        )
        return result.returncode == 0

    def _require_clean_index(self) -> None:
        result = self._git.run(
            ["git", "diff", "--cached", "--quiet", "--", "."],
            check=False,
        )
        if result.returncode == 1:
            raise RoundTransactionError(
                "Cannot begin a round transaction while the Git index contains staged changes"
            )
        if result.returncode != 0:
            raise RoundTransactionError("Could not verify that the Git index is clean")

    def _validate_active_transition(self, transition: StateTransition) -> None:
        try:
            self._active_state_slot.validate_transition(transition)
        except ProjectStateError as exc:
            raise RoundTransactionError(
                f"Invalid round transaction active-state transition: {exc}"
            ) from exc

    def _restore_active(self, transition: StateTransition) -> None:
        self._validate_active_transition(transition)
        try:
            self._active_state_slot.apply(transition)
        except ProjectStateError as exc:
            raise RoundTransactionError(
                f"Could not restore the round transaction active state: {exc}"
            ) from exc

    def _clear_journal(self) -> None:
        self._journal_slot.save(None)


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _parse_round_payload(contents: bytes, *, source: str) -> RoundRecord:
    try:
        payload = json.loads(contents)
    except (TypeError, ValueError) as exc:
        raise RoundTransactionError(
            f"Invalid completed-round payload in transaction journal {source}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RoundTransactionError(
            f"Invalid completed-round payload in transaction journal {source}: "
            "payload must be a JSON object"
        )
    try:
        return parse_round_record(payload)
    except (TypeError, ValueError, ValidationError) as exc:
        raise RoundTransactionError(
            f"Invalid completed-round payload in transaction journal {source}: {exc}"
        ) from exc
