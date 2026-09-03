#!/usr/bin/env python3
"""Fail when a Python source file grows past the god-file ceiling.

Ruff already enforces the function-level quality rules this repo cares about
(`PL`, `C901` with `max-complexity = 10`), and Biome enforces the TypeScript
equivalents plus a per-file line ceiling. Neither tool measures Python *file*
length, so a module can grow without bound while every function in it stays
inside the limits. This script closes that gap.

The ceiling is a hard limit. Every scanned file must stay within it;
the checker has no allowlist or per-file bypass.

Configuration lives in `pyproject.toml` under `[tool.vibesys.file_length]`:

    max_lines  -- ceiling, in physical lines (what `wc -l` counts).
    roots      -- repo-relative directories to scan for `*.py`. Generated
                  `__pycache__` paths are skipped.

The check fails when any scanned file exceeds `max_lines`.

Usage:
    uv run python scripts/check_file_length.py
    uv run python scripts/check_file_length.py --root /path/to/repo
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PYPROJECT = Path("pyproject.toml")
SKIPPED_DIR_NAMES = frozenset({"__pycache__"})

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_TOOL_ERROR = 2


@dataclass(frozen=True)
class Config:
    """Resolved `[tool.vibesys.file_length]` settings."""

    max_lines: int
    roots: tuple[str, ...]


class ConfigError(Exception):
    """The `[tool.vibesys.file_length]` section is missing or malformed."""


def load_config(pyproject_path: Path) -> Config:
    """Read the file-length ceiling and scan roots from ``pyproject.toml``.

    Raises:
        ConfigError: If the section is absent or a required key is missing.
    """
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"{pyproject_path}: cannot be read ({exc})") from exc  # noqa: TRY003  # tracked: #288
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{pyproject_path}: is not valid TOML ({exc})") from exc  # noqa: TRY003  # tracked: #288

    try:
        section = data["tool"]["vibesys"]["file_length"]
        max_lines = int(section["max_lines"])
        roots = tuple(str(entry) for entry in section["roots"])
    except KeyError as exc:
        raise ConfigError(  # noqa: TRY003  # tracked: #288
            f"{pyproject_path}: missing [tool.vibesys.file_length] key {exc}"
        ) from exc

    if "allowlist" in section:
        message = f"{pyproject_path}: [tool.vibesys.file_length] does not support an allowlist"
        raise ConfigError(message)
    return Config(max_lines=max_lines, roots=roots)


def count_lines(path: Path) -> int:
    """Return the number of physical lines in ``path``, matching `wc -l`."""
    return len(path.read_text(encoding="utf-8").splitlines())


def measure(repo_root: Path, roots: tuple[str, ...]) -> dict[str, int]:
    """Map every scanned repo-relative `*.py` path to its physical line count."""
    measured: dict[str, int] = {}
    for root in roots:
        for path in sorted((repo_root / root).rglob("*.py")):
            relative = path.relative_to(repo_root)
            if SKIPPED_DIR_NAMES.intersection(relative.parts):
                continue
            measured[relative.as_posix()] = count_lines(path)
    return measured


def check_measured_files(measured: dict[str, int], config: Config) -> list[str]:
    """Report files over the ceiling."""
    failures: list[str] = []
    for path, lines in sorted(measured.items()):
        if lines > config.max_lines:
            failures.append(f"  {path}: {lines} lines > {config.max_lines} (ceiling)")
    return failures


def report(failures: list[str], ceiling: int) -> int:
    """Print the outcome and return the process exit code."""
    if failures:
        print(f"Python files over the {ceiling}-line ceiling:")
        for line in failures:
            print(line)
        print("\nSplit each module until it is within the limit; bypasses are not supported.")
        return EXIT_VIOLATIONS

    print(f"All scanned Python files are within the {ceiling}-line ceiling.")
    return EXIT_OK


def main() -> int:
    """Enforce the Python file-length ceiling, returning a process exit code."""
    parser = argparse.ArgumentParser(description="Enforce the Python file-length ceiling.")
    parser.add_argument(
        "--root", type=Path, default=Path(), help="Repository root to scan (default: cwd)"
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=None,
        help="pyproject.toml holding the configuration (default: <root>/pyproject.toml)",
    )
    args = parser.parse_args()
    pyproject_path = args.pyproject if args.pyproject is not None else args.root / DEFAULT_PYPROJECT

    try:
        config = load_config(pyproject_path)
        measured = measure(args.root, config.roots)
    except (ConfigError, OSError) as exc:
        print(f"check_file_length: {exc}", file=sys.stderr)
        return EXIT_TOOL_ERROR

    failures = check_measured_files(measured, config)
    return report(failures, config.max_lines)


if __name__ == "__main__":
    raise SystemExit(main())
