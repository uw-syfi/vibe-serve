from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from vs_project_layout import (
    AmbiguousTaskError,
    InvalidTaskDefinitionError,
    InvalidTaskNameError,
    ProjectLayout,
    ProjectNotInitializedError,
    ProjectRootNotFoundError,
    TaskName,
    TaskNotFoundError,
    UnsafeProjectPathError,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_task(project: Path, name: str) -> Path:
    task = project / ".vibesys" / "tasks" / name
    task.mkdir(parents=True)
    (task / "OBJECTIVE.md").write_text("Make it fast.\n", encoding="utf-8")
    (task / "vibesys.input.toml").write_text("version = 1\n", encoding="utf-8")
    return task


def test_initialize_creates_only_authored_layout(tmp_path: Path) -> None:
    layout = ProjectLayout.open(tmp_path)

    layout.initialize()
    layout.initialize()

    assert layout.is_initialized()
    assert layout.configuration_root().path == (tmp_path / ".vibesys").resolve()
    assert layout.tasks_root().path == (tmp_path / ".vibesys" / "tasks").resolve()
    assert not layout.state_root().path.exists()
    assert not layout.evaluator_lock().path.exists()


def test_open_requires_an_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(ProjectRootNotFoundError, match="does not exist"):
        ProjectLayout.open(tmp_path / "missing")

    file_path = tmp_path / "file"
    file_path.write_text("not a project", encoding="utf-8")
    with pytest.raises(ProjectRootNotFoundError, match="not a directory"):
        ProjectLayout.open(file_path)


def test_project_is_recognized_without_generated_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "src" / "package"
    nested.mkdir(parents=True)
    _write_task(project, "latency")

    layout = ProjectLayout.discover(nested)

    assert layout.project_root.path == project.resolve()
    assert layout.is_initialized()
    assert not layout.state_root().path.exists()


def test_discover_reports_missing_project_layout(tmp_path: Path) -> None:
    nested = tmp_path / "src"
    nested.mkdir()

    with pytest.raises(ProjectNotInitializedError, match="No VibeSys project"):
        ProjectLayout.discover(nested)


def test_tasks_are_typed_validated_and_sorted(tmp_path: Path) -> None:
    beta = _write_task(tmp_path, "beta")
    alpha = _write_task(tmp_path, "alpha")
    (tmp_path / ".vibesys" / "tasks" / "README.md").write_text(
        "Task documentation.\n", encoding="utf-8"
    )

    tasks = ProjectLayout.open(tmp_path).discover_tasks()

    assert tuple(task.name for task in tasks) == (TaskName("alpha"), TaskName("beta"))
    assert tasks[0].path == alpha.resolve()
    assert tasks[0].objective_path == (alpha / "OBJECTIVE.md").resolve()
    assert tasks[0].manifest_path == (alpha / "vibesys.input.toml").resolve()
    assert tasks[1].path == beta.resolve()


def test_select_task_supports_explicit_and_single_implicit_selection(tmp_path: Path) -> None:
    task_path = _write_task(tmp_path, "throughput")
    layout = ProjectLayout.open(tmp_path)

    assert layout.select_task().path == task_path.resolve()
    assert layout.select_task("throughput").name == TaskName("throughput")
    assert layout.select_task(TaskName("throughput")).path == task_path.resolve()


def test_select_task_reports_ambiguity_with_available_names(tmp_path: Path) -> None:
    _write_task(tmp_path, "latency")
    _write_task(tmp_path, "throughput")

    with pytest.raises(AmbiguousTaskError) as caught:
        ProjectLayout.open(tmp_path).select_task()

    assert caught.value.available == (TaskName("latency"), TaskName("throughput"))
    assert "latency, throughput" in str(caught.value)


def test_select_task_reports_missing_with_available_names(tmp_path: Path) -> None:
    _write_task(tmp_path, "latency")

    with pytest.raises(TaskNotFoundError) as caught:
        ProjectLayout.open(tmp_path).select_task("throughput")

    assert caught.value.task_name == "throughput"
    assert caught.value.available == (TaskName("latency"),)


def test_select_task_reports_empty_project(tmp_path: Path) -> None:
    ProjectLayout.open(tmp_path).initialize()

    with pytest.raises(TaskNotFoundError) as caught:
        ProjectLayout.open(tmp_path).select_task()

    assert caught.value.task_name is None
    assert caught.value.available == ()


@pytest.mark.parametrize(
    "name",
    ["../task", "nested/task", "/absolute", ".", "Uppercase", "", "a" * 129],
)
def test_task_name_rejects_unsafe_and_noncanonical_values(name: str) -> None:
    with pytest.raises(InvalidTaskNameError):
        TaskName(name)


@pytest.mark.parametrize("missing_filename", ["OBJECTIVE.md", "vibesys.input.toml"])
def test_discovery_requires_objective_and_manifest(
    tmp_path: Path,
    missing_filename: str,
) -> None:
    task = _write_task(tmp_path, "latency")
    (task / missing_filename).unlink()

    with pytest.raises(InvalidTaskDefinitionError, match=missing_filename):
        ProjectLayout.open(tmp_path).discover_tasks()


def test_task_resource_resolution_rejects_traversal(tmp_path: Path) -> None:
    task_path = _write_task(tmp_path, "latency")
    benchmark = task_path / "benchmark" / "run.py"
    benchmark.parent.mkdir()
    benchmark.write_text("print('ok')\n", encoding="utf-8")
    task = ProjectLayout.open(tmp_path).select_task("latency")

    assert task.resolve("benchmark/run.py") == benchmark.resolve()
    with pytest.raises(UnsafeProjectPathError, match="safe relative path"):
        task.resolve("../OBJECTIVE.md")
    with pytest.raises(UnsafeProjectPathError, match="safe relative path"):
        task.resolve(benchmark.resolve())


def test_task_directory_symlink_cannot_escape_tasks_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tasks_root = project / ".vibesys" / "tasks"
    tasks_root.mkdir(parents=True)
    external_task = tmp_path / "external-task"
    external_task.mkdir()
    (external_task / "OBJECTIVE.md").write_text("Do work.\n", encoding="utf-8")
    (external_task / "vibesys.input.toml").write_text("version = 1\n", encoding="utf-8")
    (tasks_root / "escaped").symlink_to(external_task, target_is_directory=True)

    with pytest.raises(UnsafeProjectPathError, match="escapes"):
        ProjectLayout.open(project).discover_tasks()


def test_required_file_symlink_cannot_escape_task_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    task = _write_task(project, "latency")
    external_objective = project / "editable-objective.md"
    external_objective.write_text("Mutable objective.\n", encoding="utf-8")
    (task / "OBJECTIVE.md").unlink()
    (task / "OBJECTIVE.md").symlink_to(external_objective)

    with pytest.raises(UnsafeProjectPathError, match=r"required file OBJECTIVE\.md escapes"):
        ProjectLayout.open(project).discover_tasks()


def test_optional_task_resource_symlink_cannot_escape_task_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    task_path = _write_task(project, "latency")
    external_reference = project / "shared-reference"
    external_reference.mkdir()
    (task_path / "reference").symlink_to(external_reference, target_is_directory=True)
    task = ProjectLayout.open(project).select_task("latency")

    with pytest.raises(UnsafeProjectPathError, match="escapes"):
        task.resolve("reference")


def test_configuration_symlink_cannot_escape_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    external_configuration = tmp_path / "configuration"
    (external_configuration / "tasks").mkdir(parents=True)
    (project / ".vibesys").symlink_to(external_configuration, target_is_directory=True)

    layout = ProjectLayout.open(project)
    with pytest.raises(UnsafeProjectPathError, match="escapes"):
        layout.is_initialized()


def test_state_and_lock_symlinks_cannot_escape_configuration(tmp_path: Path) -> None:
    layout = ProjectLayout.open(tmp_path)
    layout.initialize()
    external_state = tmp_path / "external-state"
    external_state.mkdir()
    external_lock = tmp_path / "external.lock"
    external_lock.write_text("lock", encoding="utf-8")
    (tmp_path / ".vibesys" / "state").symlink_to(external_state, target_is_directory=True)
    (tmp_path / ".vibesys" / "evaluators.lock").symlink_to(external_lock)

    with pytest.raises(UnsafeProjectPathError, match="escapes"):
        layout.state_root()
    with pytest.raises(UnsafeProjectPathError, match="escapes"):
        layout.evaluator_lock()


def test_configuration_capability_rejects_path_traversal(tmp_path: Path) -> None:
    layout = ProjectLayout.open(tmp_path)
    layout.initialize()

    with pytest.raises(UnsafeProjectPathError, match="safe relative path"):
        layout.configuration_root().resolve("../outside", must_exist=False)
