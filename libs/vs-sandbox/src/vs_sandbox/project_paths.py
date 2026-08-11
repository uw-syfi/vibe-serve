"""Validated workspace-relative filesystem policy for agent projects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class ProjectPathPolicyError(ValueError):
    """Raised when a nested project path policy is invalid for its workspace."""

    @classmethod
    def invalid_workspace(cls, workspace: Path, reason: str) -> ProjectPathPolicyError:
        """Build an error about the workspace root."""
        return cls(f"workspace {reason}: {workspace}")

    @classmethod
    def invalid_path(
        cls,
        kind: str,
        path: Path,
        reason: str,
        *,
        resolved: Path | None = None,
    ) -> ProjectPathPolicyError:
        """Build an error about one declared path."""
        suffix = f" -> {resolved}" if resolved is not None else ""
        return cls(f"{kind} project path {reason}: {path}{suffix}")

    @classmethod
    def conflicting_paths(
        cls,
        first: Path,
        second: Path,
        reason: str,
    ) -> ProjectPathPolicyError:
        """Build an error about two declarations that cannot coexist."""
        return cls(f"{reason}: {first} and {second}")


@dataclass(frozen=True)
class _ResolvedProjectPath:
    """One canonical project path and the mount predicate it requires."""

    path: Path
    is_directory: bool


@dataclass(frozen=True)
class _ResolvedProjectPathPolicy:
    """Canonical project paths ready for an OS confinement backend."""

    hidden_paths: tuple[_ResolvedProjectPath, ...] = ()
    read_only_paths: tuple[_ResolvedProjectPath, ...] = ()


@dataclass(frozen=True, init=False)
class ProjectPathPolicy:
    """Nested project paths protected from an agent subprocess.

    Paths are relative to the workspace passed to :func:`vs_sandbox.build_host_sandbox`.
    Hidden paths are inaccessible on macOS and masked with an empty directory or
    file on Linux. Read-only paths remain visible but cannot be modified.

    A hidden path may be nested below a read-only path. That supports policies
    such as a readable ``.vs`` directory with a hidden ``.vs/local`` subtree.
    Other nested or duplicate declarations are rejected because they are
    redundant or assign an unreachable policy to a child path.
    """

    hidden_paths: tuple[Path, ...]
    read_only_paths: tuple[Path, ...]

    def __init__(
        self,
        *,
        hidden_paths: Iterable[Path | str] = (),
        read_only_paths: Iterable[Path | str] = (),
    ) -> None:
        """Create a policy from workspace-relative paths."""
        hidden = _normalize_relative_paths("hidden", hidden_paths)
        read_only = _normalize_relative_paths("read-only", read_only_paths)
        _validate_overlap(hidden, read_only)
        object.__setattr__(self, "hidden_paths", hidden)
        object.__setattr__(self, "read_only_paths", read_only)

    def validate(self, workspace: Path | str) -> None:
        """Validate that all declared paths exist inside *workspace*.

        Validation follows symlinks before checking containment, so a project
        symlink cannot use a relative declaration to expose or mask a host path.
        Only regular files and directories are accepted.
        """
        self.resolve(workspace)

    def resolve(self, workspace: Path | str) -> _ResolvedProjectPathPolicy:
        """Return canonical paths classified for a confinement backend."""
        if not self.hidden_paths and not self.read_only_paths:
            return _ResolvedProjectPathPolicy()

        workspace_path = Path(workspace)
        try:
            resolved_workspace = workspace_path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ProjectPathPolicyError.invalid_workspace(
                workspace_path, "does not exist"
            ) from exc
        if not resolved_workspace.is_dir():
            raise ProjectPathPolicyError.invalid_workspace(workspace_path, "is not a directory")

        hidden = tuple(
            _resolve_project_path(resolved_workspace, path, kind="hidden")
            for path in self.hidden_paths
        )
        read_only = tuple(
            _resolve_project_path(resolved_workspace, path, kind="read-only")
            for path in self.read_only_paths
        )
        _validate_overlap(
            tuple(path.path.relative_to(resolved_workspace) for path in hidden),
            tuple(path.path.relative_to(resolved_workspace) for path in read_only),
        )
        return _ResolvedProjectPathPolicy(hidden, read_only)


def _normalize_relative_paths(
    kind: str,
    paths: Iterable[Path | str],
) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_absolute():
            raise ProjectPathPolicyError.invalid_path(kind, path, "must be workspace-relative")
        if path == Path():
            raise ProjectPathPolicyError.invalid_path(kind, path, "cannot be the workspace itself")
        if ".." in path.parts:
            raise ProjectPathPolicyError.invalid_path(kind, path, "cannot contain '..'")
        normalized.append(path)
    return tuple(normalized)


def _resolve_project_path(
    workspace: Path,
    relative_path: Path,
    *,
    kind: str,
) -> _ResolvedProjectPath:
    candidate = workspace / relative_path
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ProjectPathPolicyError.invalid_path(
            kind, relative_path, "does not exist in workspace"
        ) from exc

    try:
        relative_resolved = resolved.relative_to(workspace)
    except ValueError as exc:
        raise ProjectPathPolicyError.invalid_path(
            kind, relative_path, "resolves outside workspace", resolved=resolved
        ) from exc
    if relative_resolved == Path():
        raise ProjectPathPolicyError.invalid_path(
            kind, relative_path, "resolves to the workspace itself"
        )
    if not resolved.is_file() and not resolved.is_dir():
        raise ProjectPathPolicyError.invalid_path(
            kind, relative_path, "must be a regular file or directory"
        )
    return _ResolvedProjectPath(resolved, resolved.is_dir())


def _validate_overlap(hidden_paths: tuple[Path, ...], read_only_paths: tuple[Path, ...]) -> None:
    _reject_redundant_paths("hidden", hidden_paths)
    _reject_redundant_paths("read-only", read_only_paths)

    for hidden in hidden_paths:
        for read_only in read_only_paths:
            if hidden == read_only:
                raise ProjectPathPolicyError.conflicting_paths(
                    hidden,
                    read_only,
                    "project path cannot be both hidden and read-only",
                )
            if _contains(hidden, read_only):
                raise ProjectPathPolicyError.conflicting_paths(
                    read_only,
                    hidden,
                    "read-only project path is nested below hidden path",
                )


def _reject_redundant_paths(kind: str, paths: tuple[Path, ...]) -> None:
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            if _contains(path, other) or _contains(other, path):
                raise ProjectPathPolicyError.conflicting_paths(
                    path, other, f"overlapping {kind} project paths"
                )


def _contains(parent: Path, child: Path) -> bool:
    return parent == child or parent in child.parents
