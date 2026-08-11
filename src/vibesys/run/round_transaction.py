"""Recoverable application transaction for one completed optimization round.

``ProjectStore`` owns the portable ``.vs`` filesystem contract and
``GitTracker`` owns project history. This module composes them at the
application boundary where one completed round must update both stores while
also advancing machine-local active-hypothesis state.

Callers prepare the transaction with the completed ``RoundRecord`` and desired
``active.json`` contents before mutating either local state file. A journal
below ``.vs/local`` makes every intermediate state recoverable after a process
crash. Recovery always rolls the completed round forward, preserving paid
agent work and its candidate tree.
"""

# These boundary errors deliberately name the relevant path or transaction.
# ruff: noqa: TRY003

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from vibesys.run.git_tracker import GitTracker, GitTrackingMode
from vs_loop_state import RoundRecord, parse_round_record
from vs_project_state import serialize_round

if TYPE_CHECKING:
    from vs_project_state import ProjectStore

_JOURNAL_SCHEMA_VERSION: Literal[1] = 1
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

    schema_version: Literal[1]
    run_id: str
    round_number: Annotated[int, Field(gt=0)]
    pre_commit: Annotated[str, Field(pattern=_GIT_OBJECT_ID_PATTERN)]
    next_active_contents_base64: str | None
    round_payload_base64: str
    round_payload_sha256: Annotated[str, Field(pattern=_SHA256_PATTERN)]

    @field_validator("next_active_contents_base64", "round_payload_base64")
    @classmethod
    def _validate_base64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("must contain canonical base64-encoded bytes") from exc
        return value

    def next_active_contents(self) -> bytes | None:
        """Return the completed round's active-state bytes, or ``None`` if absent."""
        if self.next_active_contents_base64 is None:
            return None
        return base64.b64decode(self.next_active_contents_base64, validate=True)

    def round_payload(self) -> bytes:
        """Return the exact portable completed-round payload."""
        return base64.b64decode(self.round_payload_base64, validate=True)


@dataclass(frozen=True)
class CompletedRound:
    """Durable outputs produced by a successful round transaction."""

    metadata_path: Path
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

    1. ``begin(record, next_active_contents)`` journals the exact portable
       round payload and desired active state.
    2. The caller updates its machine-local compatibility state.
    3. ``RoundTransaction.complete()`` writes and commits the prepared round.

    ``recover()`` is idempotent. It commits the exact journaled payload when
    needed, restores the desired active state, and then clears the journal.
    Because ``begin`` accepts only an already completed record, recovery never
    discards the result or causes its paid attempt to be replayed.
    """

    def __init__(self, store: ProjectStore, git: GitTracker, run_id: str) -> None:
        """Validate and bind the store, Git tracker, and run identity."""
        project_root = store.project_root.resolve()
        if git.root.resolve() != project_root:
            raise RoundTransactionError(
                "Round transaction store and Git tracker must use the same project root"
            )
        if git.mode is not GitTrackingMode.USER_PROJECT:
            raise RoundTransactionError("Round transactions require GitTracker user-project mode")
        if git.run_id != run_id:
            raise RoundTransactionError(
                f"Round transaction run {run_id!r} does not match Git tracker run {git.run_id!r}"
            )

        store.load_run(run_id)
        self._store = store
        self._git = git
        self.run_id = run_id

    @property
    def journal_path(self) -> Path:
        """Return the machine-local journal path for diagnostics and tests."""
        return self._store.active_state_path(self.run_id).parent / "round-transaction.json"

    def begin(
        self,
        record: RoundRecord,
        *,
        next_active_contents: bytes | None,
    ) -> RoundTransaction:
        """Durably prepare a completed round before mutating local state."""
        round_number = record.round_number
        if round_number < 1:
            raise RoundTransactionError(f"Round number must be positive, got {round_number}")
        if self.journal_path.exists() or self.journal_path.is_symlink():
            raise RoundTransactionError(
                f"An unfinished round transaction already exists at {self.journal_path}; "
                "recover it before beginning another"
            )

        round_path = self._round_path(round_number)
        if (
            round_path.exists()
            or round_path.is_symlink()
            or self._head_blob(round_path) is not None
        ):
            raise RoundTransactionError(f"Completed round metadata already exists: {round_path}")

        pre_commit = self._git.current_sha()
        if pre_commit is None:
            raise RoundTransactionError("Round transactions require an initialized Git HEAD")
        self._require_clean_index()

        round_payload = serialize_round(record)
        journal = _RoundJournal(
            schema_version=_JOURNAL_SCHEMA_VERSION,
            run_id=self.run_id,
            round_number=round_number,
            pre_commit=pre_commit,
            next_active_contents_base64=(
                base64.b64encode(next_active_contents).decode("ascii")
                if next_active_contents is not None
                else None
            ),
            round_payload_base64=base64.b64encode(round_payload).decode("ascii"),
            round_payload_sha256=_sha256(round_payload),
        )
        _atomic_write_json(self.journal_path, journal.model_dump(mode="json"))
        return RoundTransaction(self, round_number)

    def recover(self) -> RoundRecoveryOutcome:
        """Commit any journaled completed round and restore its local state."""
        if not self.journal_path.exists() and not self.journal_path.is_symlink():
            return RoundRecoveryOutcome.NO_TRANSACTION

        journal = self._load_journal()
        round_path = self._round_path(journal.round_number)
        if not self._pre_commit_is_ancestor(journal.pre_commit):
            raise RoundTransactionError(
                "Cannot recover round transaction after Git history moved away from "
                f"its starting commit {journal.pre_commit}"
            )
        committed_blob = self._head_blob(round_path)
        if committed_blob is not None:
            if _sha256(committed_blob) != journal.round_payload_sha256:
                raise RoundTransactionError(
                    f"Committed round metadata differs from transaction journal: {round_path}"
                )
            self._restore_committed_round(round_path, committed_blob)
            self._restore_active(journal.next_active_contents())
            self._clear_journal()
            return RoundRecoveryOutcome.COMMITTED

        self._commit_prepared_round(journal)
        self._restore_active(journal.next_active_contents())
        self._clear_journal()
        return RoundRecoveryOutcome.COMMITTED

    def _complete(self, round_number: int) -> CompletedRound:
        journal = self._load_journal()
        if journal.round_number != round_number:
            raise RoundTransactionError(
                f"Journal is for round {journal.round_number}, not round {round_number}"
            )

        completed = self._commit_prepared_round(journal)
        self._restore_active(journal.next_active_contents())
        self._clear_journal()
        return completed

    def _commit_prepared_round(self, journal: _RoundJournal) -> CompletedRound:
        round_payload = journal.round_payload()
        record = _parse_round_payload(round_payload, source=self.journal_path)
        if record.round_number != journal.round_number:
            raise RoundTransactionError(
                f"Round transaction journal payload is for round {record.round_number}, "
                f"not round {journal.round_number}"
            )
        round_path = self._store.save_round(self.run_id, record)
        if round_path.read_bytes() != round_payload:
            raise RoundTransactionError(
                f"Portable round metadata differs from transaction journal: {round_path}"
            )

        relative = round_path.relative_to(self._store.project_root)
        self._git.snapshot_with_framework_metadata(
            f"vibesys(round {journal.round_number}): record result",
            {relative: round_payload},
        )
        committed_blob = self._head_blob(round_path)
        if committed_blob is None or _sha256(committed_blob) != journal.round_payload_sha256:
            raise RoundTransactionError(
                f"Git snapshot did not commit the exact completed-round metadata: {round_path}"
            )
        checkpoint = self._git.current_sha()
        if checkpoint is None:
            raise RoundTransactionError("Git snapshot completed without an accessible HEAD")
        return CompletedRound(metadata_path=round_path, checkpoint=checkpoint)

    def _load_journal(self) -> _RoundJournal:
        path = self.journal_path
        if path.is_symlink():
            raise RoundTransactionError(f"Round transaction journal must not be a symlink: {path}")
        try:
            journal = _RoundJournal.model_validate_json(path.read_bytes(), strict=True)
        except FileNotFoundError as exc:
            raise RoundTransactionError(
                f"Round transaction journal does not exist: {path}"
            ) from exc
        except OSError as exc:
            raise RoundTransactionError(
                f"Could not read round transaction journal {path}: {exc}"
            ) from exc
        except ValidationError as exc:
            raise RoundTransactionError(f"Invalid round transaction journal {path}: {exc}") from exc
        if journal.run_id != self.run_id:
            raise RoundTransactionError(
                f"Round transaction journal belongs to run {journal.run_id!r}, not {self.run_id!r}"
            )
        if _sha256(journal.round_payload()) != journal.round_payload_sha256:
            raise RoundTransactionError(
                f"Round transaction journal payload digest does not match: {path}"
            )
        _parse_round_payload(journal.round_payload(), source=path)
        return journal

    def _round_path(self, round_number: int) -> Path:
        return self._store.rounds_dir(self.run_id) / f"{round_number:04d}.json"

    def _head_blob(self, path: Path) -> bytes | None:
        relative = path.relative_to(self._store.project_root).as_posix()
        result = self._git.run(["git", "show", f"HEAD:{relative}"], check=False)
        return result.stdout if result.returncode == 0 else None

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

    def _restore_active(self, contents: bytes | None) -> None:
        active_path = self._store.active_state_path(self.run_id)
        if contents is None:
            active_path.unlink(missing_ok=True)
        else:
            _atomic_write_bytes(active_path, contents)

    def _restore_committed_round(self, path: Path, contents: bytes) -> None:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != contents:
            _atomic_write_bytes(path, contents)

    def _clear_journal(self) -> None:
        self.journal_path.unlink(missing_ok=True)


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _parse_round_payload(contents: bytes, *, source: Path) -> RoundRecord:
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


def _atomic_write_json(path: Path, payload: object) -> None:
    try:
        contents = json.dumps(payload, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RoundTransactionError(
            f"Could not serialize round transaction journal {path}"
        ) from exc
    _atomic_write_bytes(path, contents + b"\n")


def _atomic_write_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
