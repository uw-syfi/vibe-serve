"""Typed filesystem boundary for the ``.vs`` project state directory.

Git owns candidate source history. This module owns only portable completed-run
metadata and machine-local operational paths. It deliberately has no knowledge
of Git, VibeSys CLI arguments, agent providers, or evaluator implementations.
"""

# These boundary errors deliberately embed the offending metadata path and value.
# ruff: noqa: TRY003

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from vs_loop_state import RoundRecord, parse_round_record, serialize_round_record

if TYPE_CHECKING:
    from uuid import UUID

PROJECT_SCHEMA_VERSION: Literal[1] = 1
_IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_GIT_OBJECT_ID_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_ROUND_FILE_PATTERN = re.compile(r"^(?P<round>0*[1-9][0-9]*)\.json$")
_EXCLUDED_NAMES = frozenset(
    {
        ".cache",
        ".git",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".vs",
        "__pycache__",
        "agent.toml",
        "node_modules",
    }
)

Identifier = Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)]
Sha256Digest = Annotated[str, Field(pattern=_DIGEST_PATTERN)]
GitObjectId = Annotated[str, Field(pattern=_GIT_OBJECT_ID_PATTERN)]
PortableText = Annotated[str, Field(min_length=1, max_length=256)]


class ProjectStateError(RuntimeError):
    """Raised when project metadata is missing, unsafe, or invalid."""


class _CommittedManifest(BaseModel):
    """Strict base for versioned, portable metadata committed with source."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]


class ProjectManifest(_CommittedManifest):
    """Immutable identity and initial provenance of one project directory."""

    project_id: Identifier
    created_at: AwareDatetime
    initial_input_fingerprint: Sha256Digest


class RunConfiguration(BaseModel):
    """Sanitized, durable agent-loop settings needed to reproduce a run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: PortableText | None = None
    outer_loop: PortableText
    inner_loop: PortableText
    interface: PortableText
    agent_backend: PortableText
    cli_provider: PortableText | None = None
    cli_timeout: Annotated[int, Field(gt=0)] | None = None
    compute_backend: PortableText
    profiler: PortableText | None = None
    max_rounds: Annotated[int, Field(gt=0)]
    max_retries_per_round: Annotated[int, Field(gt=0)]
    judge_every: Annotated[int, Field(gt=0)]
    official_eval_every: Annotated[int, Field(gt=0)]
    memory_layout: PortableText
    modality: PortableText | None = None
    default_reasoning_effort: PortableText | None = None
    outer_model: PortableText | None = None
    outer_reasoning_effort: PortableText | None = None
    inner_model: PortableText | None = None
    inner_reasoning_effort: PortableText | None = None
    operator_constraints: tuple[str, ...] = ()


class RunManifest(_CommittedManifest):
    """Immutable identity and starting provenance of one optimization run."""

    run_id: Identifier
    project_id: Identifier
    display_name: PortableText
    created_at: AwareDatetime
    input_fingerprint: Sha256Digest
    trusted_input_baseline: GitObjectId
    branch: PortableText
    vibesys_version: PortableText
    configuration: RunConfiguration


class _Digest(Protocol):
    def update(self, data: bytes, /) -> object:
        """Add bytes to the digest state."""


def generate_run_id(
    display_name: str,
    *,
    now: datetime | None = None,
    unique: UUID | None = None,
) -> str:
    """Return a sortable, path-safe run ID.

    ``now`` and ``unique`` are injectable so callers can reproduce IDs in tests.
    The display name is cosmetic: unsafe characters are normalized and an empty
    result becomes ``run``.
    """
    timestamp = now or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ProjectStateError("Run ID timestamp must include a timezone")
    timestamp = timestamp.astimezone(UTC)
    suffix = (unique or uuid.uuid4()).hex[:8]
    normalized = unicodedata.normalize("NFKD", display_name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-") or "run"
    slug = slug[:64].rstrip("-") or "run"
    return f"{timestamp:%Y%m%d-%H%M%S}-{suffix}-{slug}"


class ProjectStore:
    """Read and write portable ``.vs`` metadata below one project root."""

    def __init__(self, project_root: Path | str) -> None:
        """Bind the store to an existing project directory."""
        root = Path(project_root).resolve()
        if not root.is_dir():
            raise ProjectStateError(f"Project root is not a directory: {root}")
        self.project_root = root
        self.metadata_dir = root / ".vs"
        self.project_manifest_path = self.metadata_dir / "project.json"
        self.metadata_gitignore_path = self.metadata_dir / ".gitignore"
        self.local_dir = self.metadata_dir / "local"
        self.current_run_path = self.local_dir / "current-run"
        self._validate_storage_roots()

    def input_fingerprint(self) -> str:
        """Hash the portable project input, excluding metadata, secrets, and caches."""
        digest = hashlib.sha256(b"vs-project-input-v1\0")
        paths = sorted(
            self.project_root.rglob("*"),
            key=lambda path: path.relative_to(self.project_root).as_posix(),
        )
        for path in paths:
            relative = path.relative_to(self.project_root)
            if _is_excluded(relative):
                continue
            _update_fingerprint(digest, path, relative)
        return digest.hexdigest()

    def create_project(
        self,
        display_name: str,
        *,
        now: datetime | None = None,
    ) -> ProjectManifest:
        """Create the project manifest, or return the existing manifest unchanged."""
        self._validate_storage_roots()
        if self.project_manifest_path.exists():
            manifest = self.load_project()
            self._ensure_local_gitignore()
            return manifest
        fingerprint = self.input_fingerprint()
        manifest = ProjectManifest(
            schema_version=PROJECT_SCHEMA_VERSION,
            project_id=_project_id(display_name, fingerprint),
            created_at=_aware_now(now),
            initial_input_fingerprint=fingerprint,
        )
        _atomic_write_model(self.project_manifest_path, manifest)
        self._ensure_local_gitignore()
        return manifest

    def load_project(self) -> ProjectManifest:
        """Load the project manifest with path-specific validation errors."""
        self._validate_storage_roots()
        return _load_model(self.project_manifest_path, ProjectManifest)

    def new_run_manifest(  # noqa: PLR0913
        self,
        display_name: str,
        *,
        branch: str,
        vibesys_version: str,
        configuration: RunConfiguration,
        trusted_input_baseline: GitObjectId,
        run_id: str | None = None,
        now: datetime | None = None,
        unique: UUID | None = None,
    ) -> RunManifest:
        """Build, but do not persist, a run manifest for the current project tree."""
        project = self.load_project()
        created_at = _aware_now(now)
        return RunManifest(
            schema_version=PROJECT_SCHEMA_VERSION,
            run_id=(
                _validate_run_id(run_id)
                if run_id is not None
                else generate_run_id(display_name, now=created_at, unique=unique)
            ),
            project_id=project.project_id,
            display_name=display_name,
            created_at=created_at,
            input_fingerprint=self.input_fingerprint(),
            trusted_input_baseline=trusted_input_baseline,
            branch=branch,
            vibesys_version=vibesys_version,
            configuration=configuration,
        )

    def create_run(self, manifest: RunManifest, *, make_current: bool = True) -> None:
        """Persist a new run manifest and initialize its local operational paths."""
        self._validate_storage_roots()
        project = self.load_project()
        if manifest.project_id != project.project_id:
            raise ProjectStateError(
                f"Run {manifest.run_id!r} belongs to project {manifest.project_id!r}, "
                f"not {project.project_id!r}"
            )
        path = self.run_manifest_path(manifest.run_id)
        if path.exists():
            existing = self.load_run(manifest.run_id)
            if existing != manifest:
                raise ProjectStateError(f"Run metadata already exists with different data: {path}")
        else:
            _atomic_write_model(path, manifest)
        self.logs_dir(manifest.run_id).mkdir(parents=True, exist_ok=True)
        if make_current:
            self.set_current_run(manifest.run_id)

    def load_run(self, run_id: str) -> RunManifest:
        """Load one run manifest."""
        return _load_model(self.run_manifest_path(run_id), RunManifest)

    def update_run_configuration(
        self,
        run_id: str,
        configuration: RunConfiguration,
    ) -> Path:
        """Replace a run's sanitized configuration while preserving its identity."""
        self._validate_storage_roots()
        manifest = self.load_run(run_id)
        path = self.run_manifest_path(run_id)
        updated = manifest.model_copy(update={"configuration": configuration})
        if updated != manifest:
            _atomic_write_model(path, updated)
        return path

    def list_runs(self) -> list[RunManifest]:
        """Return all runs ordered by creation time, then run ID."""
        self._validate_storage_roots()
        runs_dir = self.metadata_dir / "runs"
        if not runs_dir.exists():
            return []
        manifests: list[RunManifest] = []
        for child in sorted(runs_dir.iterdir()):
            if not child.is_dir():
                raise ProjectStateError(f"Unexpected file in VibeSys runs directory: {child}")
            manifests.append(self.load_run(child.name))
        return sorted(manifests, key=lambda manifest: (manifest.created_at, manifest.run_id))

    def latest_run(self) -> RunManifest | None:
        """Return the most recently created run, if one exists."""
        runs = self.list_runs()
        return runs[-1] if runs else None

    def current_run_id(self) -> str | None:
        """Return the machine-local current run pointer, if it is set."""
        self._validate_storage_roots()
        if not self.current_run_path.exists():
            return None
        try:
            value = self.current_run_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ProjectStateError(
                f"Could not read current run pointer {self.current_run_path}: {exc}"
            ) from exc
        return _validate_run_id(value, source=self.current_run_path)

    def set_current_run(self, run_id: str | None) -> None:
        """Atomically update or clear the machine-local current run pointer."""
        self._validate_storage_roots()
        if run_id is None:
            self.current_run_path.unlink(missing_ok=True)
            return
        normalized = _validate_run_id(run_id)
        self.load_run(normalized)
        _atomic_write_text(self.current_run_path, f"{normalized}\n")

    def resolve_run(self, run_id: str | None = None) -> RunManifest:
        """Resolve an explicit run, otherwise current, otherwise latest."""
        if run_id is not None:
            return self.load_run(run_id)
        current = self.current_run_id()
        if current is not None:
            return self.load_run(current)
        latest = self.latest_run()
        if latest is None:
            raise ProjectStateError(f"No VibeSys runs exist under {self.metadata_dir}")
        return latest

    def save_round(self, run_id: str, record: RoundRecord) -> Path:
        """Persist one completed round without overwriting conflicting evidence."""
        self._validate_storage_roots()
        self.load_run(run_id)
        contents = serialize_round(record)
        completed = self.load_rounds(run_id)
        path = self.rounds_dir(run_id) / f"{record.round_number:04d}.json"
        if record.round_number <= len(completed):
            existing = completed[record.round_number - 1]
            if existing != record:
                raise ProjectStateError(
                    f"Completed round already exists with different data: {path}"
                )
            return path
        next_round = len(completed) + 1
        if record.round_number != next_round:
            raise ProjectStateError(
                f"Completed rounds must be appended in order: expected round {next_round}, "
                f"got {record.round_number}"
            )
        _atomic_write_text(path, contents.decode("utf-8"))
        return path

    def load_rounds(self, run_id: str) -> list[RoundRecord]:
        """Load completed rounds in numeric order."""
        self.load_run(run_id)
        directory = self.rounds_dir(run_id)
        if not directory.exists():
            return []
        numbered_paths: list[tuple[int, Path]] = []
        for path in directory.iterdir():
            match = _ROUND_FILE_PATTERN.fullmatch(path.name)
            if not path.is_file() or match is None:
                raise ProjectStateError(f"Unexpected completed-round entry: {path}")
            numbered_paths.append((int(match.group("round")), path))
        records: list[RoundRecord] = []
        for sequence_number, (file_number, path) in enumerate(sorted(numbered_paths), start=1):
            if file_number != sequence_number:
                raise ProjectStateError(
                    "Completed rounds must form a contiguous sequence starting at 1: "
                    f"expected round {sequence_number}, found {file_number} at {path}"
                )
            record = self._load_round(path)
            if record.round_number != file_number:
                raise ProjectStateError(
                    f"Round file {path} contains round {record.round_number}, "
                    f"expected {file_number}"
                )
            records.append(record)
        return records

    def run_manifest_path(self, run_id: str) -> Path:
        """Return the committed manifest path for *run_id*."""
        return self._contained_run_dir(run_id) / "run.json"

    def rounds_dir(self, run_id: str) -> Path:
        """Return the committed completed-round directory for *run_id*."""
        return self._contained_run_dir(run_id) / "rounds"

    def logs_dir(self, run_id: str) -> Path:
        """Return the uncommitted log directory for *run_id*."""
        return self._contained_local_run_dir(run_id) / "logs"

    def active_state_path(self, run_id: str) -> Path:
        """Return the uncommitted in-progress state path for *run_id*."""
        return self._contained_local_run_dir(run_id) / "active.json"

    def _contained_run_dir(self, run_id: str) -> Path:
        self._validate_storage_roots()
        normalized = _validate_run_id(run_id)
        return _contained(self.metadata_dir, self.metadata_dir / "runs" / normalized)

    def _contained_local_run_dir(self, run_id: str) -> Path:
        self._validate_storage_roots()
        normalized = _validate_run_id(run_id)
        return _contained(self.local_dir, self.local_dir / "runs" / normalized)

    def _validate_storage_roots(self) -> None:
        _validate_storage_root(self.metadata_dir, self.project_root, name="metadata")
        _validate_storage_root(self.local_dir, self.metadata_dir, name="local metadata")

    def _ensure_local_gitignore(self) -> None:
        self._validate_storage_roots()
        required = "/local/"
        try:
            existing = (
                self.metadata_gitignore_path.read_text(encoding="utf-8")
                if self.metadata_gitignore_path.exists()
                else ""
            )
        except OSError as exc:
            raise ProjectStateError(
                f"Could not read VibeSys ignore contract {self.metadata_gitignore_path}: {exc}"
            ) from exc
        if required in existing.splitlines():
            return
        separator = "" if not existing or existing.endswith("\n") else "\n"
        _atomic_write_text(self.metadata_gitignore_path, f"{existing}{separator}{required}\n")

    @staticmethod
    def _load_round(path: Path) -> RoundRecord:
        payload = _read_json_object(path)
        try:
            record = parse_round_record(payload)
        except ValidationError as exc:
            raise ProjectStateError(
                f"Invalid completed-round metadata at {path}: {_validation_message(exc)}"
            ) from exc
        _validate_portable_round(record, source=path)
        return record


def _aware_now(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ProjectStateError("Metadata timestamp must include a timezone")
    return timestamp.astimezone(UTC)


def _project_id(display_name: str, fingerprint: str) -> str:
    normalized = unicodedata.normalize("NFKD", display_name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-") or "project"
    slug = slug[:64].rstrip("-") or "project"
    return f"{slug}-{fingerprint[:12]}"


def _validate_run_id(run_id: str, *, source: Path | None = None) -> str:
    if re.fullmatch(_IDENTIFIER_PATTERN, run_id) is None:
        location = f" in {source}" if source is not None else ""
        raise ProjectStateError(
            f"Invalid VibeSys run ID{location}: {run_id!r}. "
            "Use lowercase letters, digits, dots, underscores, or hyphens."
        )
    return run_id


def _contained(parent: Path, child: Path) -> Path:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    if not child_resolved.is_relative_to(parent_resolved):
        raise ProjectStateError(f"VibeSys metadata path escapes {parent_resolved}: {child}")
    return child


def _validate_storage_root(path: Path, parent: Path, *, name: str) -> None:
    try:
        if path.is_symlink():
            raise ProjectStateError(f"VibeSys {name} root must not be a symlink: {path}")
        if path.exists() and not path.is_dir():
            raise ProjectStateError(f"VibeSys {name} root is not a directory: {path}")
        parent_resolved = parent.resolve()
        path_resolved = path.resolve()
    except OSError as exc:
        raise ProjectStateError(f"Could not validate VibeSys {name} root {path}: {exc}") from exc
    if not path_resolved.is_relative_to(parent_resolved):
        raise ProjectStateError(
            f"VibeSys {name} root escapes {parent_resolved}: {path} resolves to {path_resolved}"
        )


def _is_excluded(relative: Path) -> bool:
    for part in relative.parts:
        if part in _EXCLUDED_NAMES or part == ".env" or part.startswith(".env."):
            return True
        if part.endswith((".pyc", ".pyo")):
            return True
    return False


def _update_fingerprint(digest: _Digest, path: Path, relative: Path) -> None:
    update = digest.update
    encoded_path = relative.as_posix().encode("utf-8", "surrogateescape")
    try:
        metadata = path.lstat()
        mode = metadata.st_mode
        if stat.S_ISDIR(mode):
            update(b"D\0" + encoded_path + b"\0")
        elif stat.S_ISLNK(mode):
            target = str(path.readlink()).encode("utf-8", "surrogateescape")
            update(b"L\0" + encoded_path + b"\0" + target + b"\0")
        elif stat.S_ISREG(mode):
            executable = b"1" if mode & 0o111 else b"0"
            update(b"F\0" + encoded_path + b"\0" + executable + b"\0")
            with path.open("rb") as source:
                while block := source.read(1024 * 1024):
                    update(block)
            update(b"\0")
        else:
            raise ProjectStateError(f"Unsupported input file type: {path}")
    except OSError as exc:
        raise ProjectStateError(f"Could not fingerprint project input {path}: {exc}") from exc


def _validate_portable_round(record: RoundRecord, *, source: Path | None = None) -> None:
    """Reject machine-local paths and non-finite metrics at the commit boundary."""
    subject = f"Completed-round metadata at {source}" if source is not None else "Completed-round"
    for field_name in ("evaluation_artifact", "candidate_evaluation_artifact"):
        value = getattr(record, field_name)
        if value is None:
            continue
        artifact = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or artifact.is_absolute()
            or artifact == PurePosixPath(".")
            or ".." in artifact.parts
        ):
            raise ProjectStateError(
                f"{subject} {field_name} must be a portable project-relative path"
            )
    metric_values = [
        record.perf_metric,
        *record.metrics.values(),
        *record.candidate_metrics.values(),
    ]
    if any(value is not None and not math.isfinite(value) for value in metric_values):
        raise ProjectStateError(f"{subject} metrics must be finite numbers")


def serialize_round(record: RoundRecord) -> bytes:
    """Return validated canonical bytes for one portable completed round."""
    if record.round_number < 1:
        raise ProjectStateError(f"Round number must be positive, got {record.round_number}")
    _validate_portable_round(record)
    try:
        contents = json.dumps(
            serialize_round_record(record),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ProjectStateError(
            f"Could not serialize completed-round metadata for round {record.round_number}"
        ) from exc
    return f"{contents}\n".encode()


def _atomic_write_model(path: Path, model: BaseModel) -> None:
    _atomic_write_json(path, model.model_dump(mode="json"))


def _atomic_write_json(path: Path, payload: object) -> None:
    try:
        content = json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError) as exc:
        raise ProjectStateError(
            f"Could not serialize portable VibeSys metadata for {path}"
        ) from exc
    _atomic_write_text(path, content)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectStateError(f"VibeSys metadata file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectStateError(f"Could not read VibeSys metadata at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectStateError(f"Expected a JSON object in VibeSys metadata at {path}")
    return raw


def _load_model[ModelT: BaseModel](path: Path, model_type: type[ModelT]) -> ModelT:
    try:
        content = path.read_text(encoding="utf-8")
        return model_type.model_validate_json(content, strict=True)
    except FileNotFoundError as exc:
        raise ProjectStateError(f"VibeSys metadata file does not exist: {path}") from exc
    except OSError as exc:
        raise ProjectStateError(f"Could not read VibeSys metadata at {path}: {exc}") from exc
    except ValidationError as exc:
        raise ProjectStateError(
            f"Invalid VibeSys metadata at {path}: {_validation_message(exc)}"
        ) from exc


def _validation_message(error: ValidationError) -> str:
    failures: list[str] = []
    for detail in error.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in detail["loc"]) or "metadata"
        failures.append(f"{location}: {detail['msg']}")
    return "; ".join(failures)
