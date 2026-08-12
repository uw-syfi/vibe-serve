"""Architecture contract for physical project-state layout ownership."""

from pathlib import Path


def test_physical_state_directory_name_is_private_to_project_state_package(
    repo_root: Path,
) -> None:
    storage_name = ".v" + "s"
    owner = repo_root / "libs" / "vs-project-state"
    searched_roots = (
        repo_root / "src",
        repo_root / "scripts",
        repo_root / "libs",
        repo_root / "tests",
    )
    violations: list[str] = []

    for root in searched_roots:
        for path in root.rglob("*.py"):
            if path.is_relative_to(owner) or path == Path(__file__):
                continue
            if storage_name in path.read_text(encoding="utf-8"):
                violations.append(path.relative_to(repo_root).as_posix())

    assert violations == []
