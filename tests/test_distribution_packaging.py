"""Contracts for the Python packages owned by the ``vibesys`` distribution."""

from __future__ import annotations

import importlib.util
import subprocess
import tomllib
import zipfile
from email.parser import Parser
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
    assert package_dirs["vs_feature_flags"] == ("libs/vs-feature-flags/src/vs_feature_flags")
    assert package_dirs["vs_sandbox"] == "libs/vs-sandbox/src/vs_sandbox"


def test_namespace_discovery_excludes_build_and_cache_artifacts(tmp_path: Path) -> None:
    """Local interpreter and build artifacts must never become wheel packages."""
    module = _load_packaging_support()
    package = tmp_path / "src" / "example"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "namespace").mkdir()
    for artifact in (
        "__pycache__",
        "build",
        "dist",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    ):
        (package / artifact / "nested").mkdir(parents=True)

    packages, package_dirs = module.discover_distribution_packages(tmp_path)

    assert packages == ["example", "example.namespace"]
    assert package_dirs == {
        "example": "src/example",
        "example.namespace": "src/example/namespace",
    }


def test_root_metadata_declares_internal_runtime_dependencies_directly():  # noqa: ANN201
    """Reintroducing a workspace-only dependency must make PyPI resolution fail here."""
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    requirements = {Requirement(raw).name for raw in pyproject["project"]["dependencies"]}

    assert requirements.isdisjoint(INTERNAL_DISTRIBUTIONS)
    assert {"mcp", "modal"} <= requirements
    assert pyproject["tool"]["uv"]["workspace"]["members"] == ["sdk/*"]


def test_built_distribution_caps_dependencies_without_current_intel_macos_wheels(
    tmp_path: Path,
) -> None:
    """Published metadata must preserve constraints needed by Intel macOS installs."""
    subprocess.run(  # noqa: S603
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],  # noqa: S607
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("vibesys-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        metadata_path = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = Parser().parsestr(archive.read(metadata_path).decode())

    requirements = {
        requirement.name: requirement
        for raw in metadata.get_all("Requires-Dist", [])
        if (requirement := Requirement(raw))
    }

    assert str(requirements["cbor2"].specifier) == "<6"
    assert str(requirements["cryptography"].specifier) == "<50"
