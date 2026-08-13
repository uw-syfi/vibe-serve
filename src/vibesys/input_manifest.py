"""Command-based target input manifests."""

from __future__ import annotations

import json
import shlex
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from vibesys.domains.base import DomainName
from vibesys.evaluators import (
    EvaluatorPackageRequirement,
    load_evaluator_package_lock,
    resolve_evaluator_package,
)

if TYPE_CHECKING:
    from vs_project import Project, TaskDirectory

MANIFEST_NAME = "vibesys.input.toml"


class InputCommand(BaseModel):
    """One evaluator command declared by an input bundle."""

    model_config = ConfigDict(extra="forbid")

    command: tuple[str, ...] | None = None
    entrypoint: str | None = None
    args: tuple[str, ...] = ()
    timeout_seconds: int | None = Field(default=None, gt=0)

    @field_validator("command")
    @classmethod
    def _non_empty_command(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and not value:
            raise ValueError("command must contain at least one argv element")  # noqa: TRY003  # tracked: #288
        if value is not None and any(not part for part in value):
            raise ValueError("command elements must be non-empty strings")  # noqa: TRY003  # tracked: #288
        return value

    @field_validator("entrypoint")
    @classmethod
    def _non_empty_entrypoint(cls, value: str | None) -> str | None:
        if value is not None and (not value or any(character.isspace() for character in value)):
            raise ValueError("entrypoint must be a non-empty name without whitespace")  # noqa: TRY003
        return value

    @field_validator("args")
    @classmethod
    def _non_empty_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not part for part in value):
            raise ValueError("args elements must be non-empty strings")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def _one_command_source(self) -> InputCommand:
        if (self.command is None) == (self.entrypoint is None):
            raise ValueError("declare exactly one of command or entrypoint")  # noqa: TRY003
        if self.command is not None and self.args:
            raise ValueError("args may only be used with an evaluator entrypoint")  # noqa: TRY003
        return self

    def display(self, *, resolved_command: tuple[str, ...] | None = None) -> str:
        """Render the resolved argv used by the evaluator."""
        command = resolved_command or self.command
        if command is None:
            raise ValueError("entrypoint commands must be resolved before display")  # noqa: TRY003
        return " ".join(shlex.quote(part) for part in command)


class WorkspaceInput(BaseModel):
    """Pinned source repositories copied into a fresh candidate workspace."""

    model_config = ConfigDict(extra="forbid")

    sources: tuple[WorkspaceSource, ...] = ()


class WorkspaceSource(BaseModel):
    """Pinned git source materialized into the mutable candidate workspace."""

    model_config = ConfigDict(extra="forbid")

    name: str
    repo: str
    commit: str
    dest: str
    strip_git: bool = True

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not value:
            raise ValueError("name must be non-empty")  # noqa: TRY003  # tracked: #288
        if any(character.isspace() for character in value):
            raise ValueError("name must not contain whitespace")  # noqa: TRY003  # tracked: #288
        return value

    @field_validator("repo")
    @classmethod
    def _valid_repo(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("repo must be non-empty")  # noqa: TRY003  # tracked: #288
        parsed = urlparse(value)
        if parsed.scheme and parsed.scheme not in {"file", "http", "https", "ssh", "git"}:
            raise ValueError(f"unsupported repo URL scheme: {parsed.scheme}")  # noqa: TRY003  # tracked: #288
        return value

    @field_validator("commit")
    @classmethod
    def _valid_commit(cls, value: str) -> str:
        if not value:
            raise ValueError("commit must be non-empty")  # noqa: TRY003  # tracked: #288
        if not (7 <= len(value) <= 64) or any(c not in "0123456789abcdefABCDEF" for c in value):  # noqa: PLR2004  # tracked: #288
            raise ValueError("commit must be a 7-64 character hexadecimal hash")  # noqa: TRY003  # tracked: #288
        return value.lower()

    @field_validator("dest")
    @classmethod
    def _relative_dest(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("dest must be a non-empty path")  # noqa: TRY003  # tracked: #288
        path = Path(value)
        if path.is_absolute():
            raise ValueError("dest must be relative to the workspace")  # noqa: TRY003  # tracked: #288
        if any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("dest must not contain empty, current, or parent path components")  # noqa: TRY003  # tracked: #288
        return value


class EvaluatorInput(BaseModel):
    """A legacy source tree or an exact reusable evaluator package."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    name: str | None = None
    version: str | None = None

    @field_validator("source")
    @classmethod
    def _relative_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("source must be a non-empty path")  # noqa: TRY003  # tracked: #288
        if Path(value).is_absolute():
            raise ValueError("source must be relative to the input bundle")  # noqa: TRY003  # tracked: #288
        return value

    @model_validator(mode="after")
    def _one_evaluator_source(self) -> EvaluatorInput:
        package_values = (self.name, self.version)
        if self.source is not None:
            if any(value is not None for value in package_values):
                raise ValueError(  # noqa: TRY003
                    "evaluator source cannot be combined with name or version"
                )
            return self
        if any(value is None for value in package_values):
            raise ValueError("a packaged evaluator requires both name and version")  # noqa: TRY003
        EvaluatorPackageRequirement(name=self.name, version=self.version)  # type: ignore[arg-type]
        return self

    @property
    def package_requirement(self) -> EvaluatorPackageRequirement | None:
        """Return the exact package requirement, if this is not a legacy source."""
        if self.name is None or self.version is None:
            return None
        return EvaluatorPackageRequirement(name=self.name, version=self.version)


class BenchmarkResult(BaseModel):
    """Machine-readable scalar result emitted by a benchmark command."""

    model_config = ConfigDict(extra="forbid")

    json_argument: str
    metric: str

    @field_validator("json_argument")
    @classmethod
    def _single_option(cls, value: str) -> str:
        if not value.startswith("-") or any(character.isspace() for character in value):
            raise ValueError("json_argument must be one option-style argv element")  # noqa: TRY003  # tracked: #288
        return value

    @field_validator("metric")
    @classmethod
    def _metric_name(cls, value: str) -> str:
        if not value or any(character.isspace() for character in value):
            raise ValueError("metric must be a non-empty JSON field name without whitespace")  # noqa: TRY003  # tracked: #288
        return value


class BenchmarkCommand(InputCommand):
    """Benchmark command with an optional trusted scalar-result contract."""

    result: BenchmarkResult | None = None


class AgentInput(BaseModel):
    """Agent-loop metadata declared by an input bundle."""

    model_config = ConfigDict(extra="forbid")

    domain: DomainName


class InputManifest(BaseModel):
    """Versioned evaluator-command manifest for an input bundle."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    agent: AgentInput
    accuracy: InputCommand
    benchmark: BenchmarkCommand
    workspace: WorkspaceInput | None = None
    evaluator: EvaluatorInput | None = None

    @model_validator(mode="after")
    def _validate_cross_references(self) -> InputManifest:
        uses_entrypoint = any(
            command.entrypoint is not None for command in (self.accuracy, self.benchmark)
        )
        package = self.evaluator.package_requirement if self.evaluator is not None else None
        if uses_entrypoint and package is None:
            raise ValueError("evaluator entrypoints require a packaged [evaluator]")  # noqa: TRY003
        if not uses_entrypoint and package is not None:
            raise ValueError(  # noqa: TRY003
                "a packaged [evaluator] requires evaluator entrypoint commands"
            )
        if self.workspace is None:
            return self
        seen_names: set[str] = set()
        seen_dests: set[str] = set()
        for source in self.workspace.sources:
            if source.name in seen_names:
                raise ValueError(f"duplicate workspace source name: {source.name}")  # noqa: TRY003  # tracked: #288
            if source.dest in seen_dests:
                raise ValueError(f"duplicate workspace source destination: {source.dest}")  # noqa: TRY003  # tracked: #288
            seen_names.add(source.name)
            seen_dests.add(source.dest)
        return self


def render_input_manifest(manifest: InputManifest) -> str:  # noqa: C901
    """Serialize a validated input manifest as deterministic TOML."""

    def toml_string(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def toml_array(values: tuple[str, ...]) -> str:
        return "[" + ", ".join(toml_string(value) for value in values) + "]"

    lines = [
        f"version = {manifest.version}",
        "",
        "[agent]",
        f"domain = {toml_string(manifest.agent.domain.value)}",
        "",
        "[accuracy]",
    ]
    if manifest.accuracy.command is not None:
        lines.append(f"command = {toml_array(manifest.accuracy.command)}")
    else:
        assert manifest.accuracy.entrypoint is not None  # noqa: S101
        lines.extend(
            [
                f"entrypoint = {toml_string(manifest.accuracy.entrypoint)}",
                f"args = {toml_array(manifest.accuracy.args)}",
            ]
        )
    if manifest.accuracy.timeout_seconds is not None:
        lines.append(f"timeout_seconds = {manifest.accuracy.timeout_seconds}")

    lines.extend(
        [
            "",
            "[benchmark]",
        ]
    )
    if manifest.benchmark.command is not None:
        lines.append(f"command = {toml_array(manifest.benchmark.command)}")
    else:
        assert manifest.benchmark.entrypoint is not None  # noqa: S101
        lines.extend(
            [
                f"entrypoint = {toml_string(manifest.benchmark.entrypoint)}",
                f"args = {toml_array(manifest.benchmark.args)}",
            ]
        )
    if manifest.benchmark.timeout_seconds is not None:
        lines.append(f"timeout_seconds = {manifest.benchmark.timeout_seconds}")
    if manifest.benchmark.result is not None:
        lines.extend(
            [
                "",
                "[benchmark.result]",
                f"json_argument = {toml_string(manifest.benchmark.result.json_argument)}",
                f"metric = {toml_string(manifest.benchmark.result.metric)}",
            ]
        )

    if manifest.workspace is not None:
        for source in manifest.workspace.sources:
            lines.extend(
                [
                    "",
                    "[[workspace.sources]]",
                    f"name = {toml_string(source.name)}",
                    f"repo = {toml_string(source.repo)}",
                    f"commit = {toml_string(source.commit)}",
                    f"dest = {toml_string(source.dest)}",
                    f"strip_git = {str(source.strip_git).lower()}",
                ]
            )

    if manifest.evaluator is not None and manifest.evaluator.source is not None:
        lines.extend(
            [
                "",
                "[evaluator]",
                f"source = {toml_string(manifest.evaluator.source)}",
            ]
        )
    elif manifest.evaluator is not None:
        assert manifest.evaluator.name is not None  # noqa: S101
        assert manifest.evaluator.version is not None  # noqa: S101
        lines.extend(
            [
                "",
                "[evaluator]",
                f"name = {toml_string(manifest.evaluator.name)}",
                f"version = {toml_string(manifest.evaluator.version)}",
            ]
        )

    return "\n".join(lines) + "\n"


class InputBundle(BaseModel):
    """Resolved input bundle with manifest commands and conventional files."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path
    task_root: Path
    task_name: str | None = None
    manifest_path: Path
    objective_path: Path
    reference_path: Path | None
    evaluator_path: Path | None
    evaluator_package_digest: str | None = None
    evaluator_package_root: Path | None = None
    resolved_accuracy_command: tuple[str, ...]
    resolved_benchmark_command: tuple[str, ...]
    manifest: InputManifest

    @property
    def objective(self) -> str:  # noqa: D102  # tracked: #288
        return self.objective_path.read_text()

    @property
    def accuracy_command(self) -> tuple[str, ...]:  # noqa: D102  # tracked: #288
        return self.resolved_accuracy_command

    @property
    def benchmark_command(self) -> tuple[str, ...]:  # noqa: D102  # tracked: #288
        return self.resolved_benchmark_command

    @property
    def domain(self) -> DomainName:  # noqa: D102  # tracked: #288
        return self.manifest.agent.domain

    @property
    def accuracy_command_display(self) -> str:  # noqa: D102  # tracked: #288
        return self.manifest.accuracy.display(resolved_command=self.resolved_accuracy_command)

    @property
    def benchmark_command_display(self) -> str:  # noqa: D102  # tracked: #288
        return self.manifest.benchmark.display(resolved_command=self.resolved_benchmark_command)

    @property
    def benchmark_result(self) -> BenchmarkResult | None:  # noqa: D102  # tracked: #288
        return self.manifest.benchmark.result

    @property
    def workspace_sources(self) -> tuple[WorkspaceSource, ...]:  # noqa: D102  # tracked: #288
        if self.manifest.workspace is None:
            return ()
        return self.manifest.workspace.sources


def load_input_bundle(path: Path) -> InputBundle:
    """Load and validate a command-based input bundle.

    Evaluators are resolved exactly once relative to the manifest directory.
    They may be siblings of the bundle when the author uses ``..`` components,
    which lets a collection share large inputs without tying resolution to a
    VibeSys source checkout.
    """
    root = path.expanduser().resolve()
    return _load_input_bundle(project_root=root, task_root=root, task_name=None)


def load_project_task(project: Project, task: TaskDirectory) -> InputBundle:
    """Load one repository-native task against its candidate project root."""
    return _load_input_bundle(
        project_root=project.root,
        task_root=task.path,
        task_name=str(task.name),
        evaluator_lock_path=project.evaluator_lock().path,
        task_directory=task,
    )


def _load_input_bundle(  # noqa: C901, PLR0912, PLR0915  # tracked: #288
    *,
    project_root: Path,
    task_root: Path,
    task_name: str | None,
    evaluator_lock_path: Path | None = None,
    task_directory: TaskDirectory | None = None,
) -> InputBundle:
    """Load a manifest and resolve its commands for project-root execution."""
    root = project_root.expanduser().resolve()
    bundle_root = task_root.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"--input path does not exist: {root}")  # noqa: TRY003  # tracked: #288
    if not root.is_dir():
        raise ValueError(f"--input path is not a directory: {root}")  # noqa: TRY003  # tracked: #288

    manifest_path = (
        task_directory.manifest_path if task_directory is not None else bundle_root / MANIFEST_NAME
    )
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Input manifest not found: {manifest_path}")  # noqa: TRY003  # tracked: #288

    objective_path = (
        task_directory.objective_path
        if task_directory is not None
        else bundle_root / "OBJECTIVE.md"
    )
    if not objective_path.is_file():
        raise FileNotFoundError(f"OBJECTIVE.md not found: {objective_path}")  # noqa: TRY003  # tracked: #288

    try:
        manifest = InputManifest.model_validate(tomllib.loads(manifest_path.read_text()))
    except ValidationError as exc:
        raise ValueError(f"Invalid input manifest {manifest_path}: {exc}") from exc  # noqa: TRY003  # tracked: #288

    evaluator_package = None
    requirement = manifest.evaluator.package_requirement if manifest.evaluator is not None else None
    if requirement is not None:
        if evaluator_lock_path is not None and not evaluator_lock_path.is_file():
            raise FileNotFoundError(  # noqa: TRY003
                "Repository tasks that use evaluator packages require "
                f"an evaluator lock file: {evaluator_lock_path}"
            )
        package_lock = (
            load_evaluator_package_lock(evaluator_lock_path)
            if evaluator_lock_path is not None and evaluator_lock_path.is_file()
            else None
        )
        evaluator_package = resolve_evaluator_package(requirement, lock=package_lock)

    resolved_commands: list[tuple[str, ...]] = []
    for label, command_spec in (
        ("accuracy.command", manifest.accuracy),
        ("benchmark.command", manifest.benchmark),
    ):
        if command_spec.entrypoint is not None:
            assert evaluator_package is not None  # noqa: S101
            resolved_commands.append(
                evaluator_package.command(
                    command_spec.entrypoint,
                    *command_spec.args,
                )
            )
            continue
        command = command_spec.command
        assert command is not None  # noqa: S101
        executable = Path(command[0])
        if executable.is_absolute():
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"{label} executable must be relative to the project: {command[0]}"
            )
        if "/" not in command[0]:
            resolved_commands.append(command)
            continue
        resolved = (root / executable).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{label} executable escapes the project: {command[0]}") from exc  # noqa: TRY003  # tracked: #288
        if not resolved.exists():
            raise FileNotFoundError(f"{label} executable does not exist: {resolved}")  # noqa: TRY003  # tracked: #288
        if not resolved.is_file():
            raise ValueError(f"{label} executable is not a file: {resolved}")  # noqa: TRY003  # tracked: #288
        resolved_commands.append(command)

    reference_path = bundle_root / "reference"
    if reference_path.exists() or reference_path.is_symlink():
        reference_path = (
            task_directory.resolve("reference") if task_directory is not None else reference_path
        )
    if reference_path.exists() and not reference_path.is_dir():
        raise ValueError(f"reference path is not a directory: {reference_path}")  # noqa: TRY003  # tracked: #288
    if not reference_path.exists():
        reference_path = None

    evaluator_path = None
    if manifest.evaluator is not None and manifest.evaluator.source is not None:
        evaluator_path = (
            task_directory.resolve(manifest.evaluator.source)
            if task_directory is not None
            else (bundle_root / manifest.evaluator.source).resolve()
        )
        if not evaluator_path.exists():
            raise FileNotFoundError(f"evaluator.source path does not exist: {evaluator_path}")  # noqa: TRY003  # tracked: #288
        if not evaluator_path.is_dir():
            raise ValueError(f"evaluator.source path is not a directory: {evaluator_path}")  # noqa: TRY003  # tracked: #288

    return InputBundle(
        root=root,
        task_root=bundle_root,
        task_name=task_name,
        manifest_path=manifest_path,
        objective_path=objective_path,
        reference_path=reference_path,
        evaluator_path=evaluator_path,
        evaluator_package_digest=(
            evaluator_package.digest if evaluator_package is not None else None
        ),
        evaluator_package_root=(evaluator_package.root if evaluator_package is not None else None),
        resolved_accuracy_command=resolved_commands[0],
        resolved_benchmark_command=resolved_commands[1],
        manifest=manifest,
    )
