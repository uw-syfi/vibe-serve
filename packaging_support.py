"""Helpers that define the Python packages owned by the VibeSys distribution."""

from __future__ import annotations

from pathlib import Path

PACKAGE_SOURCE_ROOTS = (
    Path("src"),
    Path("libs/vs-feature-flags/src"),
    Path("libs/vs-github/src"),
    Path("libs/vs-issue-board/src"),
    Path("libs/vs-loop-state/src"),
    Path("libs/vs-sandbox/src"),
)


def discover_distribution_packages(repo_root: Path) -> tuple[list[str], dict[str, str]]:
    """Return every import package and its source directory for setuptools."""
    packages: list[str] = []
    package_dirs: dict[str, str] = {}

    for relative_root in PACKAGE_SOURCE_ROOTS:
        source_root = repo_root / relative_root
        discovered = sorted(
            init_file.parent.relative_to(source_root).as_posix().replace("/", ".")
            for init_file in source_root.rglob("__init__.py")
        )
        packages.extend(discovered)
        package_dirs.update(
            {
                package: (relative_root / Path(*package.split("."))).as_posix()
                for package in discovered
            }
        )

    return packages, package_dirs
