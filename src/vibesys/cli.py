"""The ``vibesys`` console entry point.

``vibesys`` is the installed-package equivalent of the in-repo ``./vs`` launcher.
By default it starts the interactive OpenTUI client and its bundled Bun runtime.
It routes to the
headless engine, with no JavaScript runtime required, when:

* ``--headless`` is passed,
* the first argument is ``validate``, or
* stdin/stdout is not a TTY (pipes, CI).

The headless path runs ``python -m vibesys`` in a subprocess; the interactive
path runs the compiled launcher under the bundled Bun executable with
``VIBESYS_PYTHON`` set so the launcher drives the current interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True)
class BundledTui:
    """Paths needed to launch the wheel's self-contained interactive client."""

    root: Path
    runtime: Path
    launcher: Path


def bundled_tui() -> BundledTui | None:
    """Return the staged TUI paths, or ``None`` for a headless source install."""
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
    """Whether to run the engine directly instead of the interactive TUI."""
    if "--headless" in args:
        return True
    if args and args[0] == "validate":
        return True
    return not (sys.stdin.isatty() and sys.stdout.isatty())


def _run_headless(args: list[str]) -> int:
    return subprocess.call([sys.executable, "-m", "vibesys", *args])  # noqa: S603  # tracked: #288


def _missing_runtime_message() -> str:
    return (
        "vibesys: the bundled Bun runtime or TUI launcher is missing or not executable.\n"
        "Reinstall the platform wheel, or run headless instead:\n"
        "  vibesys --headless --input <bundle> ..."
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``vibesys`` console script."""
    args = list(sys.argv[1:] if argv is None else argv)

    if _headless_requested(args):
        return _run_headless(args)

    bundle = bundled_tui()
    if bundle is None:
        # No TUI was built into this install; do the useful thing rather than
        # failing, and say why.
        print(  # noqa: T201  # tracked: #288
            "vibesys: interactive TUI is not bundled in this install; running "
            "headless. Install a supported platform wheel to get the TUI, or "
            "pass --headless to silence this notice.",
            file=sys.stderr,
        )
        return _run_headless(args)

    if not bundle.runtime.is_file() or not os.access(bundle.runtime, os.X_OK):
        print(_missing_runtime_message(), file=sys.stderr)  # noqa: T201  # tracked: #288
        return 1
    if not bundle.launcher.is_file():
        print(_missing_runtime_message(), file=sys.stderr)  # noqa: T201  # tracked: #288
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
