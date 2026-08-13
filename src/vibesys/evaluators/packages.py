"""Resolve immutable evaluator packages from a local package collection.

The resolver owns the evaluator package filesystem contract. Callers deal in
validated requirements and resolved packages, without depending on metadata
file names, resource layout, or content-digest implementation details.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from vibesys.resource_paths import evaluator_packages_dir

if TYPE_CHECKING:
    from pathlib import Path

EVALUATOR_PACKAGE_METADATA_NAME = "vibesys.evaluator.toml"
PACKAGE_ROOT_TOKEN = "${PACKAGE_ROOT}"  # noqa: S105
PROJECT_ROOT_TOKEN = "${PROJECT_ROOT}"  # noqa: S105

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9]+(?:[._+-][A-Za-z0-9]+)*$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_EXCLUDED_NAMES = frozenset({".git", "__pycache__", "target"})


class EvaluatorPackageError(ValueError):
    """Base error for invalid or ambiguous evaluator packages."""


class EvaluatorPackageNotFoundError(EvaluatorPackageError):
    """Raised when a local collection cannot satisfy an exact requirement."""


class EvaluatorPackageRequirement(BaseModel):
    """An exact evaluator package version requested by a task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(  # noqa: TRY003
                "name must contain lowercase letters and digits separated by '-' or '.'"
            )
        return value

    @field_validator("version")
    @classmethod
    def _valid_version(cls, value: str) -> str:
        if not _VERSION_PATTERN.fullmatch(value):
            raise ValueError(  # noqa: TRY003
                "version must be an exact package version without whitespace"
            )
        return value


class EvaluatorPackageMetadata(EvaluatorPackageRequirement):
    """Validated contents of ``vibesys.evaluator.toml``."""

    schema_version: Literal[1]
    protocol_version: Literal[1]
    toolchains: tuple[Literal["go", "rust"], ...] = ()
    entrypoints: dict[str, tuple[str, ...]]

    @field_validator("toolchains")
    @classmethod
    def _unique_toolchains(
        cls,
        value: tuple[Literal["go", "rust"], ...],
    ) -> tuple[Literal["go", "rust"], ...]:
        if len(value) != len(set(value)):
            raise ValueError("toolchains must not contain duplicates")  # noqa: TRY003
        return value

    @field_validator("entrypoints")
    @classmethod
    def _valid_entrypoints(
        cls,
        value: dict[str, tuple[str, ...]],
    ) -> dict[str, tuple[str, ...]]:
        if not value:
            raise ValueError("entrypoints must define at least one command")  # noqa: TRY003
        for name, command in value.items():
            if not _IDENTIFIER_PATTERN.fullmatch(name):
                raise ValueError(  # noqa: TRY003
                    f"entrypoint {name!r} must contain lowercase letters and digits "
                    "separated by '-' or '.'"
                )
            if not command:
                raise ValueError(  # noqa: TRY003
                    f"entrypoint {name!r} must contain at least one argv element"
                )
            if any(not part for part in command):
                raise ValueError(  # noqa: TRY003
                    f"entrypoint {name!r} contains an empty argv element"
                )
        return value


class EvaluatorPackageLockEntry(EvaluatorPackageRequirement):
    """One immutable evaluator package resolution stored in a lock file."""

    digest: str

    @field_validator("digest")
    @classmethod
    def _valid_digest(cls, value: str) -> str:
        if not _DIGEST_PATTERN.fullmatch(value):
            raise ValueError("digest must be a lowercase sha256 content digest")  # noqa: TRY003
        return value


class EvaluatorPackageLock(BaseModel):
    """Versioned repository lock for evaluator package resolutions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    package: tuple[EvaluatorPackageLockEntry, ...] = ()

    def entry(self, requirement: EvaluatorPackageRequirement) -> EvaluatorPackageLockEntry | None:
        """Return the entry matching an exact requirement, if present."""
        return next(
            (
                entry
                for entry in self.package
                if entry.name == requirement.name and entry.version == requirement.version
            ),
            None,
        )


@dataclass(frozen=True)
class ResolvedEvaluatorPackage:
    """One validated local evaluator package pinned by its content digest."""

    root: Path
    metadata: EvaluatorPackageMetadata
    digest: str

    @property
    def name(self) -> str:
        """Return the published package name."""
        return self.metadata.name

    @property
    def version(self) -> str:
        """Return the exact published package version."""
        return self.metadata.version

    def command(
        self,
        entrypoint: str,
        *arguments: str,
        project_root: Path | None = None,
    ) -> tuple[str, ...]:
        """Return an argv sequence for ``entrypoint`` plus task arguments.

        Local source packages use ``${PACKAGE_ROOT}`` in their metadata to
        remain independent of the candidate process's working directory.
        Published packages may instead map the same entrypoint to an installed
        executable on ``PATH``. Run-environment adapters supply ``project_root``
        when they need to expand ``${PROJECT_ROOT}`` in task arguments.
        """
        try:
            command = self.metadata.entrypoints[entrypoint]
        except KeyError as exc:
            available = ", ".join(sorted(self.metadata.entrypoints))
            raise EvaluatorPackageError(  # noqa: TRY003
                f"evaluator package {self.name!r} has no entrypoint {entrypoint!r}; "
                f"available entrypoints: {available}"
            ) from exc
        package_root = str(self.root)
        resolved_command = tuple(part.replace(PACKAGE_ROOT_TOKEN, package_root) for part in command)
        resolved_arguments = tuple(
            part.replace(PACKAGE_ROOT_TOKEN, package_root) for part in arguments
        )
        if project_root is None:
            return resolved_command + resolved_arguments
        candidate_root = str(project_root)
        return resolved_command + tuple(
            part.replace(PROJECT_ROOT_TOKEN, candidate_root) for part in resolved_arguments
        )


class EvaluatorPackageRegistry:
    """Resolve exact evaluator versions from one local package collection."""

    def __init__(self, root: Path) -> None:
        """Create a registry rooted at a local package collection."""
        self.root = root.expanduser().resolve()

    def resolve(self, requirement: EvaluatorPackageRequirement) -> ResolvedEvaluatorPackage:
        """Resolve one exact package requirement or raise a diagnostic error."""
        if not self.root.is_dir():
            raise EvaluatorPackageNotFoundError(  # noqa: TRY003
                f"evaluator package collection does not exist: {self.root}"
            )

        packages = self._packages()
        matches = [
            package
            for package in packages
            if package.name == requirement.name and package.version == requirement.version
        ]
        if not matches:
            available = sorted(f"{package.name}=={package.version}" for package in packages)
            detail = f"; available packages: {', '.join(available)}" if available else ""
            raise EvaluatorPackageNotFoundError(  # noqa: TRY003
                f"evaluator package {requirement.name}=={requirement.version} not found "
                f"in {self.root}{detail}"
            )
        if len(matches) > 1:
            locations = ", ".join(str(package.root) for package in matches)
            raise EvaluatorPackageError(  # noqa: TRY003
                f"duplicate evaluator package {requirement.name}=={requirement.version}: "
                f"{locations}"
            )
        return matches[0]

    def resolve_locked(
        self,
        requirement: EvaluatorPackageRequirement,
        lock: EvaluatorPackageLock | None,
    ) -> ResolvedEvaluatorPackage:
        """Resolve ``requirement`` and verify its immutable lock entry when provided."""
        package = self.resolve(requirement)
        if lock is None:
            return package
        entry = lock.entry(requirement)
        if entry is None:
            raise EvaluatorPackageError(  # noqa: TRY003
                f"evaluator package {requirement.name}=={requirement.version} is not locked"
            )
        if entry.digest != package.digest:
            raise EvaluatorPackageError(  # noqa: TRY003
                f"evaluator package {requirement.name}=={requirement.version} digest mismatch: "
                f"lock has {entry.digest}, resolved package has {package.digest}"
            )
        return package

    def _packages(self) -> tuple[ResolvedEvaluatorPackage, ...]:
        return tuple(
            load_evaluator_package(child)
            for child in sorted(self.root.iterdir(), key=lambda path: path.name)
            if child.is_dir() and (child / EVALUATOR_PACKAGE_METADATA_NAME).is_file()
        )


def load_evaluator_package(path: Path) -> ResolvedEvaluatorPackage:
    """Load one self-contained evaluator package directory."""
    root = path.expanduser().resolve()
    metadata_path = root / EVALUATOR_PACKAGE_METADATA_NAME
    if not root.is_dir():
        raise EvaluatorPackageError(  # noqa: TRY003
            f"evaluator package is not a directory: {root}"
        )
    if not metadata_path.is_file():
        raise EvaluatorPackageError(  # noqa: TRY003
            f"evaluator package metadata not found: {metadata_path}"
        )
    try:
        document = tomllib.loads(metadata_path.read_text(encoding="utf-8"))
        metadata = EvaluatorPackageMetadata.model_validate(document)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise EvaluatorPackageError(  # noqa: TRY003
            f"invalid evaluator package metadata {metadata_path}: {exc}"
        ) from exc
    return ResolvedEvaluatorPackage(
        root=root,
        metadata=metadata,
        digest=_content_digest(root),
    )


def resolve_evaluator_package(
    requirement: EvaluatorPackageRequirement,
    *,
    packages_root: Path | None = None,
    lock: EvaluatorPackageLock | None = None,
) -> ResolvedEvaluatorPackage:
    """Resolve a package from an explicit collection or VibeSys resources.

    When ``lock`` is supplied, resolution fails unless the exact package and
    its resolved content digest match the lock file.
    """
    root = packages_root if packages_root is not None else evaluator_packages_dir()
    if root is None:
        raise EvaluatorPackageNotFoundError(  # noqa: TRY003
            "VibeSys evaluator package resources are not available; install a complete "
            "VibeSys distribution or pass packages_root"
        )
    return EvaluatorPackageRegistry(root).resolve_locked(requirement, lock)


def load_evaluator_package_lock(path: Path) -> EvaluatorPackageLock:
    """Load and validate one evaluator package lock file."""
    lock_path = path.expanduser().resolve()
    try:
        document = tomllib.loads(lock_path.read_text(encoding="utf-8"))
        lock = EvaluatorPackageLock.model_validate(document)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise EvaluatorPackageError(  # noqa: TRY003
            f"invalid evaluator package lock {lock_path}: {exc}"
        ) from exc
    duplicate_keys = sorted(
        key
        for key in {(entry.name, entry.version) for entry in lock.package}
        if sum(1 for entry in lock.package if (entry.name, entry.version) == key) > 1
    )
    if duplicate_keys:
        duplicates = ", ".join(f"{name}=={version}" for name, version in duplicate_keys)
        raise EvaluatorPackageError(  # noqa: TRY003
            f"duplicate evaluator package lock entries in {lock_path}: {duplicates}"
        )
    return lock


def render_evaluator_package_lock(lock: EvaluatorPackageLock) -> str:
    """Serialize an evaluator lock deterministically as TOML."""
    lines = [f"schema_version = {lock.schema_version}"]
    for entry in sorted(lock.package, key=lambda item: (item.name, item.version)):
        lines.extend(
            [
                "",
                "[[package]]",
                f"name = {json.dumps(entry.name)}",
                f"version = {json.dumps(entry.version)}",
                f"digest = {json.dumps(entry.digest)}",
            ]
        )
    return "\n".join(lines) + "\n"


def write_evaluator_package_lock(path: Path, lock: EvaluatorPackageLock) -> None:
    """Write ``lock`` atomically, creating its parent directory as needed."""
    lock_path = path.expanduser().resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = lock_path.with_name(f".{lock_path.name}.tmp")
    try:
        temporary.write_text(render_evaluator_package_lock(lock), encoding="utf-8")
        temporary.replace(lock_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _content_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        root.rglob("*"), key=lambda candidate: candidate.relative_to(root).as_posix()
    ):
        relative = path.relative_to(root)
        if any(part in _DIGEST_EXCLUDED_NAMES for part in relative.parts):
            continue
        if path.is_symlink():
            raise EvaluatorPackageError(  # noqa: TRY003
                f"evaluator packages may not contain symlinks: {path}"
            )
        if not path.is_file() or path.suffix == ".pyc":
            continue
        relative_bytes = relative.as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update((path.stat().st_mode & 0o111).to_bytes(2, "big"))
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"
