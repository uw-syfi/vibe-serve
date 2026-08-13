"""The ``vibesys`` console entry point (unified launcher).

``vibesys`` is the single entry point for both installed and in-repo use, and
replaces the former ``./vs`` script. From a source checkout, run it with
``uv run vibesys``.

It routes to the headless engine, with no JavaScript runtime required, when:

* ``--headless`` is passed,
* the first argument is ``validate``, or
* stdin/stdout is not a TTY (pipes, CI).

Otherwise it starts the interactive OpenTUI client, resolving the TUI in order:

1. a prebuilt payload bundled into the wheel (``vibesys/_tui``), run under its
   vendored Bun -- the hermetic end-user path;
2. a source checkout (``clients/tui``), built on demand with the system JS
   toolchain (Bun + Node 20+ + pnpm) -- the developer path that subsumes
   ``./vs``;
3. otherwise, a notice plus the headless engine.

The headless path runs ``python -m vibesys`` in a subprocess. Interactive paths
run the compiled launcher with ``VIBESYS_PYTHON`` set so it drives the current
interpreter.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import NoReturn

_MIN_NODE_MAJOR = 20

#: Files and directories whose changes trigger a source-TUI rebuild, relative to
#: the repository root. Mirrors the staleness check the old ``./vs`` script used.
_REBUILD_WATCH_FILES: tuple[str, ...] = (
    "clients/tui/package.json",
    "clients/tui/tsconfig.json",
    "clients/tui/tsconfig.check.json",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "biome.json",
)
_REBUILD_WATCH_DIRS: tuple[str, ...] = ("clients/tui/src", "src/vibesys/server")


@dataclass(frozen=True)
class BundledTui:
    """Paths needed to launch the wheel's self-contained interactive client."""

    root: Path
    runtime: Path
    launcher: Path


def bundled_tui() -> BundledTui | None:
    """Return the staged prebuilt TUI paths, or ``None`` when not bundled."""
    try:
        base = Path(str(files("vibesys"))) / "_tui"
    except (ModuleNotFoundError, TypeError):  # pragma: no cover - defensive
        return None
    if not base.is_dir():
        return None
    return BundledTui(
        root=base,
        runtime=base / "bin" / "bun",
        launcher=base / "app" / "dist" / "launcher.js",
    )


def _headless_requested(args: list[str]) -> bool:
    """Whether to run the engine directly instead of the interactive TUI.

    ``--help``/``-h`` and ``validate`` never need the TUI, so they always go to
    the engine (and never trigger a source-checkout build).
    """
    if "--headless" in args or "--help" in args or "-h" in args:
        return True
    if args and args[0] == "validate":
        return True
    return not (sys.stdin.isatty() and sys.stdout.isatty())


def _run_headless(args: list[str]) -> int:
    return subprocess.call([sys.executable, "-m", "vibesys", *args])  # noqa: S603  # tracked: #288


# ---------------------------------------------------------------------------
# Tier 1: prebuilt payload bundled into a platform wheel
# ---------------------------------------------------------------------------


def _bundled_runtime_missing_message() -> str:
    return (
        "vibesys: the bundled Bun runtime or TUI launcher is missing or not executable.\n"
        "Reinstall the platform wheel, or run headless instead:\n"
        "  vibesys --headless --project /path/to/repository --task TASK ..."
    )


def _run_bundled_tui(bundle: BundledTui, args: list[str]) -> int:
    if not bundle.runtime.is_file() or not os.access(bundle.runtime, os.X_OK):
        print(_bundled_runtime_missing_message(), file=sys.stderr)  # noqa: T201  # tracked: #288
        return 1
    if not bundle.launcher.is_file():
        print(_bundled_runtime_missing_message(), file=sys.stderr)  # noqa: T201  # tracked: #288
        return 1

    env = {
        **os.environ,
        "BUN_CONFIG_SKIP_INSTALL_PACKAGES": "1",
        "VIBESYS_PYTHON": sys.executable,
        "VIBESYS_TUI_RUNTIME": str(bundle.runtime),
    }
    return subprocess.call(  # noqa: S603  # tracked: #288
        [str(bundle.runtime), str(bundle.launcher), *args],
        env=env,
    )


# ---------------------------------------------------------------------------
# Tier 2: build and run the TUI from a source checkout (subsumes ./vs)
# ---------------------------------------------------------------------------


def _reject_json_constant(value: str) -> NoReturn:
    message = "Invalid JSON constant"
    raise json.JSONDecodeError(message, value, 0)


def source_checkout_root() -> Path | None:
    """Return the VibeSys checkout owning this module, or ``None``."""
    try:
        package_file = Path(__file__).resolve()
        root = package_file.parents[2]
        if (root / "src" / "vibesys" / "cli.py").resolve() != package_file:
            return None

        with (root / "pyproject.toml").open("rb") as pyproject_file:
            pyproject = tomllib.load(pyproject_file)
        project = pyproject.get("project")
        if not isinstance(project, dict) or project.get("name") != "vibesys":
            return None

        with (root / "clients" / "tui" / "package.json").open(encoding="utf-8") as package_file:
            tui_package = json.load(package_file, parse_constant=_reject_json_constant)
        if not isinstance(tui_package, dict) or tui_package.get("name") != "@vibesys/tui":
            return None
    except (IndexError, OSError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return None
    return root


def _bun_executable() -> Path | None:
    found = shutil.which("bun")
    if found is not None:
        return Path(found)
    fallback = Path.home() / ".bun" / "bin" / "bun"
    return fallback if fallback.is_file() and os.access(fallback, os.X_OK) else None


def _node_executable() -> Path | None:
    found = shutil.which("node")
    return Path(found) if found is not None else None


def _node_major(node: Path) -> int | None:
    try:
        result = subprocess.run(  # noqa: S603  # tracked: #288
            [str(node), "--version"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.match(r"v(\d+)", result.stdout.strip())
    return int(match.group(1)) if match else None


def _pnpm_argv() -> list[str] | None:
    pnpm = shutil.which("pnpm")
    if pnpm is not None:
        return [pnpm]
    corepack = shutil.which("corepack")
    if corepack is not None:
        return [corepack, "pnpm"]
    return None


def _needs_rebuild(root: Path) -> bool:
    dist = root / "clients" / "tui" / "dist"
    entry = dist / "index.js"
    launcher = dist / "launcher.js"
    if not entry.is_file() or not launcher.is_file():
        return True
    reference = entry.stat().st_mtime
    for rel in _REBUILD_WATCH_FILES:
        path = root / rel
        if path.is_file() and path.stat().st_mtime > reference:
            return True
    for rel in _REBUILD_WATCH_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.stat().st_mtime > reference:
                return True
    return False


def _ensure_source_tui_built(root: Path) -> bool:
    pnpm = _pnpm_argv()
    if pnpm is None:
        print(  # noqa: T201  # tracked: #288
            "vibesys: pnpm is required to build the interactive client. Install pnpm "
            "or enable Corepack, or run headless with --headless.",
            file=sys.stderr,
        )
        return False
    print("vibesys: building the interactive client...", file=sys.stderr)  # noqa: T201  # tracked: #288
    steps = (
        [*pnpm, "install", "--frozen-lockfile"],
        [*pnpm, "--dir", "clients/tui", "generate:protocol"],
        [*pnpm, "--dir", "clients/tui", "build"],
    )
    for command in steps:
        result = subprocess.run(  # noqa: S603  # tracked: #288
            command, cwd=str(root), capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            sys.stderr.write("vibesys: failed to build the interactive client:\n")
            sys.stderr.write(result.stdout)
            sys.stderr.write(result.stderr)
            return False
    return True


def _run_source_tui(root: Path, args: list[str]) -> int:
    bun = _bun_executable()
    if bun is None:
        print(  # noqa: T201  # tracked: #288
            "vibesys: Bun is required by the OpenTUI client. Install it from "
            "https://bun.sh, or run headless with --headless.",
            file=sys.stderr,
        )
        return 1
    node = _node_executable()
    if node is None or (_node_major(node) or 0) < _MIN_NODE_MAJOR:
        print(  # noqa: T201  # tracked: #288
            f"vibesys: Node.js {_MIN_NODE_MAJOR}+ is required for the interactive client, "
            "or run headless with --headless.",
            file=sys.stderr,
        )
        return 1
    if _needs_rebuild(root) and not _ensure_source_tui_built(root):
        return 1

    launcher = root / "clients" / "tui" / "dist" / "launcher.js"
    env = {
        **os.environ,
        "VIBESYS_PYTHON": sys.executable,
        # Ensure the launcher's Bun frontend is discoverable even when Bun only
        # lives under ~/.bun/bin.
        "PATH": os.pathsep.join([str(bun.parent), os.environ.get("PATH", "")]),
    }
    return subprocess.call(  # noqa: S603  # tracked: #288
        [str(node), str(launcher), *args], env=env
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``vibesys`` console script."""
    args = list(sys.argv[1:] if argv is None else argv)

    if _headless_requested(args):
        return _run_headless(args)

    bundle = bundled_tui()
    if bundle is not None:
        return _run_bundled_tui(bundle, args)

    root = source_checkout_root()
    if root is not None:
        return _run_source_tui(root, args)

    print(  # noqa: T201  # tracked: #288
        "vibesys: interactive TUI is not bundled and no source checkout was found; "
        "running headless. Install a supported platform wheel to get the TUI, or "
        "pass --headless to silence this notice.",
        file=sys.stderr,
    )
    return _run_headless(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
