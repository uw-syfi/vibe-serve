"""Architecture contracts for physical VibeSys project ownership."""

import re
from pathlib import Path


def test_physical_configuration_root_is_private_to_project_library(
    repo_root: Path,
) -> None:
    configuration_name = ".vibe" + "sys"
    path_construction = re.compile(
        rf"(?:Path\(\s*|/\s*)['\"]{re.escape(configuration_name)}(?:['\"]|/)"
    )
    owner = repo_root / "libs" / "vs-project"
    searched_roots = (
        repo_root / "src",
        repo_root / "scripts",
        repo_root / "libs",
    )
    violations: list[str] = []

    for root in searched_roots:
        for path in root.rglob("*.py"):
            if path.is_relative_to(owner):
                continue
            if path_construction.search(path.read_text(encoding="utf-8")):
                violations.append(path.relative_to(repo_root).as_posix())

    assert violations == []
