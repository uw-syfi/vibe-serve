"""Contracts for the Python packages owned by the ``vibesys`` distribution."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.requirements import Requirement

if TYPE_CHECKING:
    from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTERNAL_DISTRIBUTIONS = {
    "vs-feature-flags",
    "vs-github",
    "vs-issue-board",
    "vs-loop-state",
    "vs-sandbox",
}
INTERNAL_IMPORT_PACKAGES = {
    "vs_feature_flags",
    "vs_github",
    "vs_issue_board",
    "vs_loop_state",
    "vs_sandbox",
}


def _load_packaging_support() -> ModuleType:
    module_path = PROJECT_ROOT / "packaging_support.py"
    assert module_path.is_file()
    spec = importlib.util.spec_from_file_location("packaging_support", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_distribution_discovers_internal_packages_from_their_source_roots():  # noqa: ANN201
    """Dropping one source root must make its import package disappear from the wheel."""
    module = _load_packaging_support()
    packages, package_dirs = module.discover_distribution_packages(PROJECT_ROOT)

    assert {"vibesys", *INTERNAL_IMPORT_PACKAGES} <= set(packages)
    assert "vibesys.prompts.backend.cuda" in packages
    assert package_dirs["vibesys"] == "src/vibesys"
    assert package_dirs["vs_feature_flags"] == (
        "libs/vs-feature-flags/src/vs_feature_flags"
    )
    assert package_dirs["vs_sandbox"] == "libs/vs-sandbox/src/vs_sandbox"


def test_root_metadata_declares_internal_runtime_dependencies_directly():  # noqa: ANN201
    """Reintroducing a workspace-only dependency must make PyPI resolution fail here."""
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    requirements = {
        Requirement(raw).name for raw in pyproject["project"]["dependencies"]
    }

    assert requirements.isdisjoint(INTERNAL_DISTRIBUTIONS)
    assert {"mcp", "modal"} <= requirements
    assert pyproject["tool"]["uv"]["workspace"]["members"] == ["sdk/*"]
