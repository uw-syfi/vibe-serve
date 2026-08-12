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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

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


class StateModelNotFoundError(ProjectStateError):
    """Raised when a required model is absent from a state namespace."""


@dataclass(frozen=True)
class StateDocument:
    """One immutable, validated JSON state document at a project-relative path."""

    project_relative_path: PurePosixPath
    contents: bytes

    def __post_init__(self) -> None:
        """Require a safe ``.vs`` path and a JSON object payload."""
        _validate_project_state_path(self.project_relative_path)
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.contents, bytes
        ):
            raise TypeError("state document contents must be bytes")
        try:
            payload = json.loads(self.contents)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("state document contents must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise TypeError("state document contents must be a JSON object")


@dataclass(frozen=True)
class StateTransition:
    """An immutable replacement or deletion of one ``.vs`` state document."""

    project_relative_path: PurePosixPath
    next_document: StateDocument | None

    def __post_init__(self) -> None:
        """Require a safe target and an exactly matching replacement document."""
        _validate_project_state_path(self.project_relative_path)
        if (
            self.next_document is not None
            and self.next_document.project_relative_path != self.project_relative_path
        ):
            raise ValueError("state transition document path must match its target path")


@dataclass(frozen=True)
class StateFile:
    """One immutable file in a portable state snapshot.

    ``relative_path`` is relative to the snapshot root. Snapshot
    consumers must combine it with :attr:`StateSnapshot.namespace_root`, never
    with an unvalidated filesystem path.
    """

    relative_path: PurePosixPath
    contents: bytes

    def __post_init__(self) -> None:
        """Reject unsafe paths and mutable or textual contents."""
        _validate_snapshot_relative_path(self.relative_path)
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.contents, bytes
        ):
            raise TypeError("state snapshot file contents must be bytes")


@dataclass(frozen=True)
class StateSnapshot:
    """Deterministic, immutable selection of portable ``.vs`` files.

    The root is ``.vs``, one run directory, or one run-state namespace. File
    paths are relative to that root. The combined paths can never address
    machine-local state below ``.vs/local``.
    """

    namespace_root: PurePosixPath
    files: tuple[StateFile, ...]

    def __post_init__(self) -> None:
        """Require a safe portable root and ordered unique files."""
        _validate_snapshot_root(self.namespace_root)
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.files, tuple
        ):
            raise TypeError("state snapshot files must be an immutable tuple")
        if any(
            not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                item, StateFile
            )
            for item in self.files
        ):
            raise TypeError("state snapshot files must contain StateFile values")
        paths = tuple(item.relative_path for item in self.files)
        if paths != tuple(sorted(paths, key=PurePosixPath.as_posix)):
            raise ValueError("state snapshot files must be ordered by relative path")
        if len(paths) != len(set(paths)):
            raise ValueError("state snapshot files must have unique relative paths")
        for path in paths:
            combined = self.namespace_root / path
            if combined.parts[:2] == (".vs", "local"):
                raise ValueError("portable state snapshots must not contain .vs/local files")


class StateNamespace:
    """Opaque, safe filesystem boundary for one run-state namespace.

    Instances are created by :class:`ProjectStore`. Callers address files only
    by namespace-relative portable paths and exchange validated Pydantic models.
    Machine-local namespaces support the same model operations but cannot be
    converted into portable snapshots.
    """

    __slots__ = ("_kind", "_portable", "_project_root", "_root")

    def __init__(
        self,
        *,
        project_root: Path,
        root: Path,
        portable: bool,
    ) -> None:
        """Bind one validated project-owned namespace root."""
        self._project_root = project_root
        self._root = root
        self._portable = portable
        self._kind = "portable" if portable else "local"
        self._validated_root()

    def load[ModelT: BaseModel](
        self,
        relative_path: str | PurePosixPath,
        model_type: type[ModelT],
    ) -> ModelT:
        """Load and strictly validate one required state model."""
        path = self._resolve_file(relative_path)
        return _load_state_model(path, model_type)

    def load_optional[ModelT: BaseModel](
        self,
        relative_path: str | PurePosixPath,
        model_type: type[ModelT],
    ) -> ModelT | None:
        """Return a valid state model, or ``None`` only when it is absent.

        Malformed JSON and model validation failures remain errors. They are
        never conflated with a missing optional checkpoint.
        """
        path = self._resolve_file(relative_path)
        try:
            return _load_state_model(path, model_type)
        except StateModelNotFoundError:
            return None

    def save(self, relative_path: str | PurePosixPath, model: BaseModel) -> None:
        """Atomically serialize one state model at a safe relative path."""
        self.apply(self.transition(relative_path, model))

    def slot[ModelT: BaseModel](
        self,
        relative_path: str | PurePosixPath,
        model_type: type[ModelT],
    ) -> StateSlot[ModelT]:
        """Bind one path and model schema as a reusable typed state slot."""
        return StateSlot(self, relative_path, model_type)

    def transition(
        self,
        relative_path: str | PurePosixPath,
        model: BaseModel | None,
    ) -> StateTransition:
        """Prepare an immutable replacement or deletion without applying it."""
        path = self._resolve_file(relative_path)
        project_relative_path = PurePosixPath(path.relative_to(self._project_root).as_posix())
        document = (
            None
            if model is None
            else StateDocument(
                project_relative_path=project_relative_path,
                contents=_serialize_state_model(model),
            )
        )
        return StateTransition(
            project_relative_path=project_relative_path,
            next_document=document,
        )

    def apply(self, transition: StateTransition) -> None:
        """Atomically apply a transition prepared for this namespace."""
        root = self._validated_root()
        namespace_root = PurePosixPath(root.relative_to(self._project_root).as_posix())
        try:
            relative_path = transition.project_relative_path.relative_to(namespace_root)
        except ValueError as exc:
            raise ProjectStateError(
                f"State transition target is outside this namespace: "
                f"{transition.project_relative_path}"
            ) from exc
        path = self._resolve_file(relative_path)
        try:
            if transition.next_document is None:
                if path.exists() and not path.is_file():
                    raise ProjectStateError(f"VibeSys state path is not a file: {path}")
                path.unlink(missing_ok=True)
            else:
                _atomic_write_bytes(path, transition.next_document.contents)
        except OSError as exc:
            raise ProjectStateError(
                f"Could not apply VibeSys state transition at {path}: {exc}"
            ) from exc

    def delete(self, relative_path: str | PurePosixPath) -> bool:
        """Delete one state file, returning whether it existed."""
        path = self._resolve_file(relative_path)
        if not path.exists():
            return False
        if not path.is_file():
            raise ProjectStateError(f"VibeSys state path is not a file: {path}")
        try:
            path.unlink()
        except OSError as exc:
            raise ProjectStateError(
                f"Could not delete VibeSys state model at {path}: {exc}"
            ) from exc
        return True

    def snapshot(self) -> StateSnapshot:
        """Return an ordered immutable snapshot of this portable namespace."""
        if not self._portable:
            raise ProjectStateError("Machine-local VibeSys state namespaces cannot be snapshotted")
        root = self._validated_root()
        namespace_root = PurePosixPath(root.relative_to(self._project_root).as_posix())
        if not root.exists():
            return StateSnapshot(namespace_root=namespace_root, files=())

        files: list[StateFile] = []
        try:
            paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
            for path in paths:
                relative = path.relative_to(root)
                if path.is_symlink():
                    raise ProjectStateError(
                        f"VibeSys {self._kind} state must not contain symlinks: {path}"
                    )
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise ProjectStateError(
                        f"VibeSys {self._kind} state contains an unsupported file type: {path}"
                    )
                files.append(
                    StateFile(
                        relative_path=PurePosixPath(relative.as_posix()),
                        contents=path.read_bytes(),
                    )
                )
        except OSError as exc:
            raise ProjectStateError(
                f"Could not snapshot VibeSys {self._kind} state at {root}: {exc}"
            ) from exc
        return StateSnapshot(namespace_root=namespace_root, files=tuple(files))

    def project_relative_path(
        self, relative_path: str | PurePosixPath | None = None
    ) -> PurePosixPath:
        """Return a safe project-relative location for display or external APIs.

        Filesystem reads and writes must still use this namespace's typed
        methods. This representation exists for agent prompts and external
        libraries whose contracts require a path.
        """
        root = self._validated_root()
        result = PurePosixPath(root.relative_to(self._project_root).as_posix())
        if relative_path is not None:
            result /= _validate_state_relative_path(relative_path)
        return result

    def external_directory(self, relative_directory: str | PurePosixPath | None = None) -> Path:
        """Materialize a safe directory for an external path-based API.

        Framework-owned model persistence should use :meth:`load` and
        :meth:`save`. This escape hatch is only for external libraries whose
        contracts require them to manage a directory tree directly.
        """
        root = self._validated_root()
        directory = (
            root
            if relative_directory is None
            else _contained_without_symlinks(
                root,
                root.joinpath(*_validate_state_relative_path(relative_directory).parts),
                kind=f"{self._kind} external state directory",
            )
        )
        try:
            if directory.exists() and not directory.is_dir():
                raise ProjectStateError(
                    f"VibeSys {self._kind} external state path is not a directory: {directory}"
                )
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProjectStateError(
                f"Could not create VibeSys {self._kind} external state directory {directory}: {exc}"
            ) from exc
        return directory

    def _validated_root(self) -> Path:
        root = _contained_without_symlinks(
            self._project_root,
            self._root,
            kind=f"{self._kind} state namespace",
        )
        try:
            if root.exists() and not root.is_dir():
                raise ProjectStateError(
                    f"VibeSys {self._kind} state namespace is not a directory: {root}"
                )
        except OSError as exc:
            raise ProjectStateError(
                f"Could not validate VibeSys {self._kind} state namespace {root}: {exc}"
            ) from exc
        return root

    def _resolve_file(self, relative_path: str | PurePosixPath) -> Path:
        root = self._validated_root()
        relative = _validate_state_relative_path(relative_path)
        path = root.joinpath(*relative.parts)
        return _contained_without_symlinks(root, path, kind=f"{self._kind} state file")


class StateSlot[ModelT: BaseModel]:
    """One schema-bound state file within a namespace.

    Externally reconstructed transitions must pass through this boundary before
    they are applied. This ensures recovery code cannot restore a JSON object
    that violates the owning subsystem's model schema.
    """

    __slots__ = ("_model_type", "_namespace", "_relative_path")

    def __init__(
        self,
        namespace: StateNamespace,
        relative_path: str | PurePosixPath,
        model_type: type[ModelT],
    ) -> None:
        """Bind a validated namespace path to one Pydantic model type."""
        self._namespace = namespace
        self._relative_path = _validate_state_relative_path(relative_path)
        self._model_type = model_type

    @property
    def project_relative_path(self) -> PurePosixPath:
        """Return this slot's validated project-relative path."""
        return self._namespace.project_relative_path(self._relative_path)

    def load_optional(self) -> ModelT | None:
        """Load this slot, returning ``None`` only when it is absent."""
        return self._namespace.load_optional(self._relative_path, self._model_type)

    def transition(self, model: ModelT | None) -> StateTransition:
        """Prepare an exact replacement or deletion for this slot."""
        return self._namespace.transition(self._relative_path, model)

    def save(self, model: ModelT | None) -> None:
        """Atomically save or clear this slot."""
        self.apply(self.transition(model))

    def validate_transition(self, transition: StateTransition) -> StateTransition:
        """Validate a reconstructed transition's target and replacement schema."""
        if transition.project_relative_path != self.project_relative_path:
            raise ProjectStateError(
                "State transition must target the typed slot: "
                f"expected {self.project_relative_path}, got "
                f"{transition.project_relative_path}"
            )
        if transition.next_document is not None:
            try:
                self._model_type.model_validate_json(
                    transition.next_document.contents,
                    strict=True,
                )
            except ValidationError as exc:
                raise ProjectStateError(
                    "State transition document does not match the typed slot schema at "
                    f"{self.project_relative_path}: {exc}"
                ) from exc
        return transition

    def apply(self, transition: StateTransition) -> None:
        """Validate and atomically apply a transition to this slot."""
        self._namespace.apply(self.validate_transition(transition))


class _CommittedManifest(BaseModel):
    """Strict base for versioned, portable metadata committed with source."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]


class ProjectManifest(_CommittedManifest):
    """Immutable identity and initial provenance of one project directory."""

    project_id: Identifier
    created_at: AwareDatetime
    initial_input_fingerprint: Sha256Digest


class _BaseRunConfiguration(BaseModel):
    """Strict settings shared by every supported outer loop."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: PortableText | None = None
    agent_backend: PortableText
    cli_provider: PortableText | None = None
    cli_timeout: Annotated[int, Field(gt=0)] | None = None
    compute_backend: PortableText
    profiler: PortableText | None = None
    modality: PortableText | None = None
    default_reasoning_effort: PortableText | None = None
    outer_model: PortableText | None = None
    outer_reasoning_effort: PortableText | None = None
    inner_model: PortableText | None = None
    inner_reasoning_effort: PortableText | None = None


class AgentRunConfiguration(_BaseRunConfiguration):
    """Sanitized settings that define an agent-loop run."""

    outer_loop: Literal["agent"]
    inner_loop: PortableText
    interface: PortableText
    max_rounds: Annotated[int, Field(gt=0)]
    max_retries_per_round: Annotated[int, Field(gt=0)]
    judge_every: Annotated[int, Field(gt=0)]
    official_eval_every: Annotated[int, Field(gt=0)]
    memory_layout: PortableText
    operator_constraints: tuple[str, ...] = ()


class PlainRunConfiguration(_BaseRunConfiguration):
    """Sanitized settings that define an issue-driven plain-loop run."""

    outer_loop: Literal["plain"]
    max_rounds: Annotated[int, Field(gt=0)]
    max_attempts_per_issue: Annotated[int, Field(gt=0)]
    max_issues_per_perf_eval: Annotated[int, Field(gt=0)]


class EvolveRunConfiguration(_BaseRunConfiguration):
    """Sanitized settings that define an evolutionary-search run."""

    outer_loop: Literal["evolve"]
    max_generations: Annotated[int, Field(gt=0)]
    children_per_generation: Annotated[int, Field(gt=0)]
    k_top_inspirations: Annotated[int, Field(ge=0)]
    k_random_inspirations: Annotated[int, Field(ge=0)]
    selection_temperature: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    seed: int | None = None
    search_policy: Literal["vibesys", "openevolve"] | None = None
    openevolve_population_size: Annotated[int, Field(gt=0)] | None = None
    openevolve_archive_size: Annotated[int, Field(gt=0)] | None = None
    openevolve_num_islands: Annotated[int, Field(gt=0)] | None = None
    openevolve_migration_interval: Annotated[int, Field(gt=0)] | None = None
    openevolve_migration_rate: Annotated[float, Field(ge=0, le=1)] | None = None
    frontier_bias: Annotated[float, Field(ge=0, le=1)]
    bootstrap_max_attempts: Annotated[int, Field(gt=0)]
    keep_deployments: bool
    max_parallelism: Annotated[int, Field(gt=0)]
    objectives: tuple[PortableText, ...] = ()

    @model_validator(mode="after")
    def _validate_search_policy_settings(self) -> Self:
        openevolve_values = (
            self.openevolve_population_size,
            self.openevolve_archive_size,
            self.openevolve_num_islands,
            self.openevolve_migration_interval,
            self.openevolve_migration_rate,
        )
        if self.search_policy == "vibesys" and any(
            value is not None for value in openevolve_values
        ):
            raise ValueError("OpenEvolve settings require search_policy='openevolve'")
        return self


RunConfiguration = Annotated[
    AgentRunConfiguration | PlainRunConfiguration | EvolveRunConfiguration,
    Field(discriminator="outer_loop"),
]


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

    def initialization_snapshot(self, run_id: str) -> StateSnapshot:
        """Snapshot the metadata required to initialize one project run in Git."""
        self.load_project()
        self.load_run(run_id)
        return _snapshot_selected_files(
            project_root=self.project_root,
            root=self.metadata_dir,
            paths=(
                self.metadata_gitignore_path,
                self.project_manifest_path,
                self.run_manifest_path(run_id),
            ),
        )

    def load_run(self, run_id: str) -> RunManifest:
        """Load one run manifest."""
        return _load_model(self.run_manifest_path(run_id), RunManifest)

    def run_manifest_snapshot(self, run_id: str) -> StateSnapshot:
        """Snapshot the current portable manifest for one run."""
        self.load_run(run_id)
        path = self.run_manifest_path(run_id)
        return _snapshot_selected_files(
            project_root=self.project_root,
            root=path.parent,
            paths=(path,),
        )

    def update_run_configuration(
        self,
        run_id: str,
        configuration: RunConfiguration,
    ) -> Path:
        """Replace a run's sanitized configuration while preserving its identity."""
        self._validate_storage_roots()
        manifest = self.load_run(run_id)
        if configuration.outer_loop != manifest.configuration.outer_loop:
            raise ProjectStateError(
                f"Run {manifest.run_id!r} uses outer loop "
                f"{manifest.configuration.outer_loop!r}, not {configuration.outer_loop!r}"
            )
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

    def completed_round_snapshot(self, run_id: str, round_number: int) -> StateSnapshot:
        """Snapshot one validated completed-round record."""
        if round_number < 1:
            raise ProjectStateError(f"Round number must be positive, got {round_number}")
        records = self.load_rounds(run_id)
        if round_number > len(records):
            raise ProjectStateError(
                f"Completed round {round_number} does not exist for run {run_id!r}"
            )
        root = self._portable_state_dir(run_id, "agent")
        path = self.rounds_dir(run_id) / f"{round_number:04d}.json"
        return _snapshot_selected_files(
            project_root=self.project_root,
            root=root,
            paths=(path,),
        )

    def run_manifest_path(self, run_id: str) -> Path:
        """Return the committed manifest path for *run_id*."""
        return self._contained_run_dir(run_id) / "run.json"

    def rounds_dir(self, run_id: str) -> Path:
        """Return the agent loop's committed completed-round directory."""
        return _contained_state_dir(
            self._portable_state_dir(run_id, "agent"),
            "rounds",
            kind="completed-round",
        )

    def _portable_state_dir(self, run_id: str, namespace: str) -> Path:
        """Return one loop or subsystem's portable state directory."""
        return _contained_state_dir(
            self._contained_run_dir(run_id),
            namespace,
            kind="portable",
        )

    def portable_namespace(self, run_id: str, namespace: str) -> StateNamespace:
        """Return the typed filesystem boundary for committed subsystem state."""
        self.load_run(run_id)
        return StateNamespace(
            project_root=self.project_root,
            root=self._portable_state_dir(run_id, namespace),
            portable=True,
        )

    def _local_state_dir(self, run_id: str, namespace: str) -> Path:
        """Return one loop or subsystem's machine-local state directory."""
        return _contained_state_dir(
            self._contained_local_run_dir(run_id),
            namespace,
            kind="local",
        )

    def local_namespace(self, run_id: str, namespace: str) -> StateNamespace:
        """Return the typed filesystem boundary for machine-local subsystem state."""
        self.load_run(run_id)
        return StateNamespace(
            project_root=self.project_root,
            root=self._local_state_dir(run_id, namespace),
            portable=False,
        )

    def logs_dir(self, run_id: str) -> Path:
        """Return the uncommitted log directory for *run_id*."""
        return self._contained_local_run_dir(run_id) / "logs"

    def round_transaction_path(self, run_id: str) -> Path:
        """Return the machine-local round commit transaction path."""
        return self._contained_local_run_dir(run_id) / "round-transaction.json"

    def worktrees_dir(self, run_id: str) -> Path:
        """Return the machine-local directory reserved for candidate worktrees."""
        return _contained_state_dir(
            self._contained_local_run_dir(run_id),
            "worktrees",
            kind="worktrees",
        )

    def _contained_run_dir(self, run_id: str) -> Path:
        self._validate_storage_roots()
        normalized = _validate_run_id(run_id)
        return _contained_without_symlinks(
            self.metadata_dir,
            self.metadata_dir / "runs" / normalized,
            kind="portable run",
        )

    def _contained_local_run_dir(self, run_id: str) -> Path:
        self._validate_storage_roots()
        normalized = _validate_run_id(run_id)
        return _contained_without_symlinks(
            self.local_dir,
            self.local_dir / "runs" / normalized,
            kind="local run",
        )

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


def _validate_namespace(namespace: str) -> str:
    if re.fullmatch(_IDENTIFIER_PATTERN, namespace) is None:
        raise ProjectStateError(
            f"Invalid VibeSys state namespace: {namespace!r}. "
            "Use lowercase letters, digits, dots, underscores, or hyphens."
        )
    return namespace


def _validate_state_relative_path(raw_path: str | PurePosixPath) -> PurePosixPath:
    if isinstance(raw_path, str):
        value = raw_path
        if not value or "\\" in value or any(not part for part in value.split("/")):
            raise ProjectStateError(
                f"VibeSys state file path must be a non-empty portable relative path: {raw_path!r}"
            )
    else:
        value = raw_path.as_posix()
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path == PurePosixPath(".")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ProjectStateError(
            f"VibeSys state file path must be a safe portable relative path: {raw_path!r}"
        )
    return path


def _validate_project_state_path(path: PurePosixPath) -> None:
    if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        path, PurePosixPath
    ):
        raise TypeError("state document paths must be PurePosixPath values")
    try:
        _validate_state_relative_path(path)
    except ProjectStateError as exc:
        raise ValueError(str(exc)) from exc
    if path.parts[:1] != (".vs",) or path == PurePosixPath(".vs"):
        raise ValueError("state document paths must identify a file below .vs")


def _validate_snapshot_relative_path(path: PurePosixPath) -> None:
    if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        path, PurePosixPath
    ):
        raise TypeError("state snapshot paths must be PurePosixPath values")
    try:
        _validate_state_relative_path(path)
    except ProjectStateError as exc:
        raise ValueError(str(exc)) from exc


def _validate_snapshot_root(path: PurePosixPath) -> None:
    _validate_snapshot_relative_path(path)
    parts = path.parts
    if parts == (".vs",):
        return
    if parts[:2] == (".vs", "local"):
        raise ValueError("portable state snapshot root must not be below .vs/local")
    if parts[:2] != (".vs", "runs") or len(parts) not in {3, 4}:
        raise ValueError(
            "portable state snapshot root must be .vs, .vs/runs/<run-id>, "
            "or .vs/runs/<run-id>/<namespace>"
        )
    if re.fullmatch(_IDENTIFIER_PATTERN, parts[2]) is None:
        raise ValueError(f"portable state snapshot root contains an invalid run ID: {path}")
    if len(parts) == 4 and re.fullmatch(_IDENTIFIER_PATTERN, parts[3]) is None:  # noqa: PLR2004
        raise ValueError(f"portable state snapshot root contains an invalid namespace: {path}")


def _snapshot_selected_files(
    *,
    project_root: Path,
    root: Path,
    paths: tuple[Path, ...],
) -> StateSnapshot:
    snapshot_root = _contained_without_symlinks(
        project_root,
        root,
        kind="portable snapshot root",
    )
    files: list[StateFile] = []
    try:
        for raw_path in paths:
            path = _contained_without_symlinks(
                snapshot_root,
                raw_path,
                kind="portable snapshot file",
            )
            if not path.is_file():
                if not path.exists():
                    raise ProjectStateError(
                        f"Portable VibeSys snapshot file does not exist: {path}"
                    )
                raise ProjectStateError(f"Portable VibeSys snapshot path is not a file: {path}")
            files.append(
                StateFile(
                    relative_path=PurePosixPath(path.relative_to(snapshot_root).as_posix()),
                    contents=path.read_bytes(),
                )
            )
    except OSError as exc:
        raise ProjectStateError(
            f"Could not read portable VibeSys snapshot below {snapshot_root}: {exc}"
        ) from exc
    files.sort(key=lambda item: item.relative_path.as_posix())
    return StateSnapshot(
        namespace_root=PurePosixPath(snapshot_root.relative_to(project_root).as_posix()),
        files=tuple(files),
    )


def _contained_state_dir(parent: Path, namespace: str, *, kind: str) -> Path:
    path = _contained_without_symlinks(
        parent,
        parent / _validate_namespace(namespace),
        kind=f"{kind} state",
    )
    try:
        if path.exists() and not path.is_dir():
            raise ProjectStateError(f"VibeSys {kind} state path is not a directory: {path}")
    except OSError as exc:
        raise ProjectStateError(
            f"Could not validate VibeSys {kind} state directory {path}: {exc}"
        ) from exc
    return path


def _contained_without_symlinks(parent: Path, child: Path, *, kind: str) -> Path:
    path = _contained(parent, child)
    current = parent
    for component in child.relative_to(parent).parts:
        current /= component
        try:
            if current.is_symlink():
                raise ProjectStateError(f"VibeSys {kind} path must not be a symlink: {current}")
        except OSError as exc:
            raise ProjectStateError(
                f"Could not validate VibeSys {kind} path {current}: {exc}"
            ) from exc
    return path


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


def _serialize_state_model(model: BaseModel) -> bytes:
    try:
        content = json.dumps(
            model.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ProjectStateError("Could not serialize VibeSys state model") from exc
    return f"{content}\n".encode()


def _atomic_write_model(path: Path, model: BaseModel) -> None:
    _atomic_write_bytes(path, _serialize_state_model(model))


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


def _load_state_model[ModelT: BaseModel](path: Path, model_type: type[ModelT]) -> ModelT:
    try:
        content = path.read_text(encoding="utf-8")
        return model_type.model_validate_json(content, strict=True)
    except FileNotFoundError as exc:
        raise StateModelNotFoundError(f"VibeSys state model does not exist: {path}") from exc
    except (OSError, UnicodeError) as exc:
        raise ProjectStateError(f"Could not read VibeSys state model at {path}: {exc}") from exc
    except ValidationError as exc:
        raise ProjectStateError(
            f"Invalid VibeSys state model at {path}: {_validation_message(exc)}"
        ) from exc


def _validation_message(error: ValidationError) -> str:
    failures: list[str] = []
    for detail in error.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in detail["loc"]) or "metadata"
        failures.append(f"{location}: {detail['msg']}")
    return "; ".join(failures)
