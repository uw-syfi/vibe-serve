from pathlib import Path

from vibesys.run.git_tracker import GitTracker
from vibesys.run.project_policy import (
    TRUSTED_PROJECT_INPUT_PATHS,
    build_project_path_policy,
)
from vs_project_state import ProjectStore


def test_trusted_project_paths_have_one_application_owner() -> None:
    assert GitTracker._TRUSTED_INPUT_PATHS == TRUSTED_PROJECT_INPUT_PATHS  # noqa: SLF001


def test_project_policy_protects_trusted_inputs_and_hides_local_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    for relative in TRUSTED_PROJECT_INPUT_PATHS:
        path = project / relative
        if path.suffix:
            path.write_text("trusted\n")
        else:
            path.mkdir()
    (project / ".git").mkdir()
    store = ProjectStore(project)
    store.create_project("test")
    store.model_cache_directory("test").mkdir(parents=True)
    (project / "agent.toml").write_text("private\n")
    (project / ".env.local").write_text("TOKEN=private\n")

    policy = build_project_path_policy(
        project,
        evaluator_source=project / "_evaluator" / "nested",
    )
    state_paths = store.sandbox_paths()

    assert set(policy.read_only_paths) == {
        Path(".git"),
        state_paths.read_only_path,
        *(Path(relative) for relative in TRUSTED_PROJECT_INPUT_PATHS),
    }
    assert set(policy.hidden_paths) == {
        state_paths.hidden_path,
        Path(".env.local"),
        Path("agent.toml"),
    }


def test_project_policy_ignores_external_evaluator_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "evaluator"
    external.mkdir()

    policy = build_project_path_policy(
        project,
        evaluator_source=external,
    )

    assert policy.read_only_paths == ()
    assert policy.hidden_paths == ()
