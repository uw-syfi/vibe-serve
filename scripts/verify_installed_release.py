"""Verify a VibeSys tool installation without access to its source checkout."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import shutil
import site
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Never

from vibesys.cli import bundled_tui
from vibesys.input_project import materialize_input_project
from vibesys.profilers import ACTIVE_PROFILER_KINDS
from vibesys.resource_paths import (
    default_skill_roots,
    profiler_support_dir,
    resources_root,
)

FRAMEWORK_PACKAGES = (
    "vibesys",
    "vs_feature_flags",
    "vs_github",
    "vs_issue_board",
    "vs_loop_state",
    "vs_sandbox",
)
SYSTEM_JAVASCRIPT_TOOLS = ("bun", "node", "npm", "pnpm")


def resolved_runtime_root(path: Path | None = None) -> Path:
    """Resolve the clean-room root, including macOS's ``/tmp`` symlink."""
    configured = path or Path(os.environ.get("VIBESYS_RELEASE_RUNTIME_ROOT", "/tmp"))  # noqa: S108
    return configured.resolve()


_RUNTIME_ROOT = resolved_runtime_root()


class InstalledReleaseError(RuntimeError):
    """Raised when an installed release is incomplete or contaminated."""

    @classmethod
    def command_failed(cls, command: list[str]) -> InstalledReleaseError:
        """Build an error for a failed installed-artifact command."""
        return cls(f"Installed verification command failed: {command}")


def verify_installed_release() -> None:
    """Exercise the installed framework, SDK, resources, and native TUI."""
    _verify_isolated_interpreter()
    _verify_framework_imports()
    verify_console_entry_point()
    _verify_resources()
    _verify_materialized_sdk()
    _verify_tui()


def _verify_isolated_interpreter() -> None:
    if os.environ.get("PYTHONNOUSERSITE") != "1" or site.ENABLE_USER_SITE:
        _fail("User-site packages are not disabled")
    if os.environ.get("PYTHONPATH"):
        _fail("PYTHONPATH must be empty")
    for executable in SYSTEM_JAVASCRIPT_TOOLS:
        if shutil.which(executable) is not None:
            _fail(f"System JavaScript runtime is present on PATH: {executable}")
    home = Path(os.environ.get("HOME", ""))
    if not home.is_dir() or any(home.iterdir()):
        _fail(f"HOME must be an empty isolated directory: {home}")
    if Path.cwd().resolve() != _RUNTIME_ROOT:
        _fail(f"Installed verification must run from {_RUNTIME_ROOT}, not {Path.cwd().resolve()}")


def _verify_framework_imports() -> None:
    distributions = importlib.metadata.packages_distributions()
    for package in FRAMEWORK_PACKAGES:
        module = importlib.import_module(package)
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            _fail(f"Installed package has no import path: {package}")
        resolved = Path(module_file).resolve()
        if "site-packages" not in str(resolved):
            _fail(f"Package did not import from site-packages: {package} -> {resolved}")
        if distributions.get(package) != ["vibesys"]:
            _fail(
                f"Package {package} is not owned only by the vibesys distribution: "
                f"{distributions.get(package)}"
            )


def verify_console_entry_point() -> None:
    """Exercise the installed ``vibesys`` console script in headless mode."""
    executable = shutil.which("vibesys")
    if executable is None:
        _fail("Installed vibesys console script is absent from PATH")
    _run([executable, "--headless", "--help"], timeout=30)


def _verify_resources() -> None:
    root = resources_root()
    if root is None or "site-packages" not in str(root.resolve()):
        _fail(f"Installed resources did not resolve from site-packages: {root}")
    skill_roots = default_skill_roots()
    if len(skill_roots) != 1 or not any(skill_roots[0].rglob("SKILL.md")):
        _fail(f"Installed skills did not resolve: {skill_roots}")
    for kind in ACTIVE_PROFILER_KINDS:
        support = profiler_support_dir(kind.value)
        if support is None or "site-packages" not in str(support.resolve()):
            _fail(f"Installed profiler resources did not resolve for {kind.value}: {support}")


def _verify_materialized_sdk() -> None:
    with tempfile.TemporaryDirectory(
        prefix="vibesys-installed-input-", dir=_RUNTIME_ROOT
    ) as temporary:
        root = Path(temporary)
        project_root = root / "missing-checkout"
        input_project = project_root / "examples" / "model-serving" / "input"
        input_project.mkdir(parents=True)
        (input_project / "pyproject.toml").write_text(
            "[project]\n"
            "name = 'installed-release-input'\n"
            "version = '0.1.0'\n"
            "requires-python = '>=3.12'\n"
            "dependencies = ['vs-bench']\n"
            "\n"
            "[tool.uv.sources]\n"
            "vs-bench = { path = '../../../sdk/vs-bench' }\n"
        )
        workspace = root / "workspace"
        workspace.mkdir()
        dependencies = materialize_input_project(
            input_project,
            workspace,
            project_root=project_root,
            copy_dir=_copy_tree,
        )
        if [dependency.name for dependency in dependencies] != ["vs-bench"]:
            _fail(f"Packaged SDK did not materialize: {dependencies}")
        source_path = dependencies[0].source_path.resolve()
        if "site-packages" not in str(source_path):
            _fail(f"SDK source did not resolve from the installed wheel: {source_path}")

        _run(build_sdk_sync_command(workspace), timeout=180)
        sdk_python = workspace / ".venv" / "bin" / "python"
        _run(
            [
                str(sdk_python),
                "-c",
                "import vs_bench; print(vs_bench.__file__)",
            ],
            timeout=30,
        )


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True)


def build_sdk_sync_command(workspace: Path) -> list[str]:
    """Build the nested SDK sync command with the isolated interpreter."""
    return [
        "uv",
        "sync",
        "--no-cache",
        "--no-config",
        "--python",
        sys.executable,
        "--project",
        str(workspace),
    ]


def _verify_tui() -> None:
    bundle = bundled_tui()
    if bundle is None:
        _fail("Installed release has no bundled TUI")
    if "site-packages" not in str(bundle.root.resolve()):
        _fail(f"Bundled TUI did not resolve from site-packages: {bundle.root}")
    environment = {
        **os.environ,
        "BUN_CONFIG_SKIP_INSTALL_PACKAGES": "1",
        "VIBESYS_PYTHON": sys.executable,
        "VIBESYS_TUI_RUNTIME": str(bundle.runtime),
    }
    _run(
        [str(bundle.runtime), str(bundle.root / "app" / "dist" / "self-test.js")],
        env=environment,
        timeout=30,
    )
    _run(
        [str(bundle.runtime), str(bundle.launcher), "--backend", "cpu", "--help"],
        env=environment,
        timeout=30,
    )


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int,
) -> None:
    try:
        subprocess.run(  # noqa: S603
            command,
            env=env,
            check=True,
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise InstalledReleaseError.command_failed(command) from exc


def _fail(message: str) -> Never:
    raise InstalledReleaseError(message)


def main() -> int:
    """Run installed verification with concise diagnostics."""
    try:
        verify_installed_release()
    except InstalledReleaseError as exc:
        print(f"installed release verification failed: {exc}", file=sys.stderr)
        return 1
    print("installed release verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
