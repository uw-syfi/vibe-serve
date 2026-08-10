"""Contracts for native installed-release isolation."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from scripts.verify_installed_release import (  # pyright: ignore[reportMissingImports]
    build_sdk_sync_command,
    resolved_runtime_root,
    verify_console_entry_point,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_runtime_root_uses_the_resolved_filesystem_path(tmp_path: Path) -> None:
    apparent = tmp_path / "tmp"
    actual = tmp_path / "private" / "tmp"
    actual.mkdir(parents=True)
    apparent.symlink_to(actual, target_is_directory=True)

    assert resolved_runtime_root(apparent) == actual.resolve()


def test_console_entry_point_runs_installed_command_with_headless_help(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "args"
    executable = tmp_path / "vibesys"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$VIBESYS_TEST_ARGS"\n')
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("VIBESYS_TEST_ARGS", str(marker))

    verify_console_entry_point()

    assert marker.read_text().splitlines() == ["--headless", "--help"]


def test_sdk_sync_uses_the_running_isolated_interpreter(tmp_path: Path) -> None:
    command = build_sdk_sync_command(tmp_path / "workspace")

    assert command == [
        "uv",
        "sync",
        "--no-cache",
        "--no-config",
        "--python",
        sys.executable,
        "--project",
        str(tmp_path / "workspace"),
    ]
