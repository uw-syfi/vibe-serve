"""Stage bundled framework resources for inclusion in the Python wheel.

Profiler support packages (``resources/profilers``) and the preset skills
library (``resources/skills``) are resolved from the repository checkout at
run time. An installed wheel has no checkout, so the wheel build copies both
trees into the ``vibesys._resources`` package directory; ``setup.py`` calls
:func:`stage_resources` from the same custom ``build_py`` step that stages the
TUI. ``vibesys.resource_paths`` resolves the checkout first and falls back to
the staged copy.

Vendored skill repository checkouts (``repos/`` inside a skill) are excluded:
they are git submodules, are already excluded from workspace materialization,
and would bloat the wheel by hundreds of megabytes.

Like ``tui_packaging``, this module is imported inside an isolated build
environment, so it depends only on the standard library.
"""

from __future__ import annotations

import shutil
from pathlib import Path  # noqa: TC003  # tracked: #288

#: Subtrees of ``resources/`` staged into the wheel.
STAGED_TREES: tuple[str, ...] = ("profilers", "skills")

#: Directory names never copied, at any depth.
EXCLUDED_NAMES: frozenset[str] = frozenset({".git", "__pycache__", "repos"})


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDED_NAMES or name.endswith(".pyc")}


def stage_resources(repo_root: Path, dest: Path) -> bool:
    """Copy the staged resource trees into ``dest``; return whether staged.

    Returns ``False`` without side effects when the checkout has no
    ``resources/`` directory (e.g. a build from an incomplete source tree);
    the wheel then installs without bundled resources and run time falls back
    to requiring a checkout, exactly as before staging existed.
    """
    source_root = repo_root / "resources"
    if not source_root.is_dir():
        return False
    if dest.exists():
        shutil.rmtree(dest)
    staged_any = False
    for tree in STAGED_TREES:
        source = source_root / tree
        if not source.is_dir():
            continue
        shutil.copytree(source, dest / tree, ignore=_ignore)
        staged_any = True
    return staged_any
