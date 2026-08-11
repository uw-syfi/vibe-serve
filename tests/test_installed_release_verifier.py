"""Contracts for native installed-release isolation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from scripts import verify_installed_release as verifier  # pyright: ignore[reportMissingImports]

from vibesys.cli import BundledTui


def test_runtime_root_uses_the_resolved_filesystem_path(tmp_path: Path) -> None:
    apparent = tmp_path / "tmp"
    actual = tmp_path / "private" / "tmp"
    actual.mkdir(parents=True)
    apparent.symlink_to(actual, target_is_directory=True)

    assert verifier.resolved_runtime_root(apparent) == actual.resolve()


def test_console_entry_points_run_installed_commands_with_help(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vibesys_marker = tmp_path / "vibesys-args"
    mcp_marker = tmp_path / "mcp-args"
    vibesys = tmp_path / "vibesys"
    vibesys.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$VIBESYS_TEST_ARGS"\n')
    vibesys.chmod(0o755)
    issue_mcp = tmp_path / "vibesys-issue-mcp"
    issue_mcp.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$VIBESYS_MCP_TEST_ARGS"\n')
    issue_mcp.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("VIBESYS_TEST_ARGS", str(vibesys_marker))
    monkeypatch.setenv("VIBESYS_MCP_TEST_ARGS", str(mcp_marker))

    verifier.verify_console_entry_point()

    assert vibesys_marker.read_text().splitlines() == ["--headless", "--help"]
    assert mcp_marker.read_text().splitlines() == ["--help"]


def test_first_launch_defaults_use_user_owned_launch_directory_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[list[str], int]] = []
    executable = tmp_path / "bin" / "vibesys"

    def run_capture(command: list[str], *, timeout: int) -> str:
        observed.append((command, timeout))
        config_path = tmp_path / "agent.toml"
        config_text = config_path.read_text()
        assert 'visibility = "public"' in config_text
        assert "owner" not in config_text
        return json.dumps(
            {
                "runs_dir": str(tmp_path / "exp_env"),
                "input_path": "",
                "experiment_name": "experiment-20260811-120000",
                "repository_owner": None,
                "repository_name": "experiment-20260811-120000",
                "visibility": "public",
                "theme": "solarized-light",
            }
        )

    monkeypatch.setattr(verifier, "_RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(verifier, "_run_capture", run_capture)
    monkeypatch.setattr(
        verifier.shutil,
        "which",
        lambda name: str(executable) if name == "vibesys" else None,
    )

    verifier.verify_first_launch_defaults()

    assert observed == [([str(executable), "tui-defaults"], 30)]
    assert not (tmp_path / "agent.toml").exists()


def test_sdk_sync_uses_the_running_isolated_interpreter(tmp_path: Path) -> None:
    command = verifier.build_sdk_sync_command(tmp_path / "workspace")

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


def test_required_system_tools_reject_missing_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verifier.shutil, "which", lambda _executable: None)

    with pytest.raises(verifier.InstalledReleaseError, match="git"):
        verifier._verify_required_system_tools()  # noqa: SLF001


def test_tui_verification_checks_controller_startup_and_completed_headless_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = tmp_path / "observed.json"
    bundle_root = tmp_path / "site-packages" / "vibesys" / "_tui"
    self_test = bundle_root / "app" / "dist" / "self-test.js"
    self_test.parent.mkdir(parents=True)
    self_test.write_text("raise SystemExit(0)\n")
    launcher = self_test.with_name("launcher.js")
    launcher.write_text("raise SystemExit(0)\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_cli = fake_bin / "vibesys"
    fake_cli.write_text(
        """\
#!__PYTHON__
import json
import os
import sys
import tomllib
from pathlib import Path

arguments = sys.argv[1:]
input_index = arguments.index("--input")
input_root = Path(arguments[input_index + 1])
runs_index = arguments.index("--runs-dir")
runs_root = Path(arguments[runs_index + 1])
headless = "--headless" in arguments
if headless:
    (runs_root / "20260811-120000-installed-release-smoke").mkdir(parents=True)
else:
    marker = Path(os.environ["VIBESYS_RELEASE_SMOKE_MARKER"])
    marker.write_text("renderer initialized; control protocol exchanged\\n")
normalized_arguments = list(arguments)
normalized_arguments[input_index + 1] = "<temporary-input>"
normalized_arguments[runs_index + 1] = "<temporary-runs>"
manifest = tomllib.loads((input_root / "vibesys.input.toml").read_text())
record = {
    "normalized_argv": normalized_arguments,
    "runs_share_smoke_root": runs_root.parent == input_root.parent,
    "input_files": sorted(path.name for path in input_root.iterdir()),
    "manifest_commands": [manifest["accuracy"]["command"], manifest["benchmark"]["command"]],
}
if not headless:
    record["ttys"] = [os.isatty(fd) for fd in (0, 1, 2)]
observed_path = Path(os.environ["VIBESYS_TEST_OBSERVED"])
observed = json.loads(observed_path.read_text()) if observed_path.exists() else []
observed.append(record)
observed_path.write_text(json.dumps(observed))
""".replace("__PYTHON__", sys.executable)
    )
    fake_cli.chmod(0o755)
    monkeypatch.setattr(
        verifier,
        "bundled_tui",
        lambda: BundledTui(root=bundle_root, runtime=Path(sys.executable), launcher=launcher),
    )
    monkeypatch.setattr(verifier, "_RUNTIME_ROOT", tmp_path)
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setenv("VIBESYS_TEST_OBSERVED", str(observed))

    verifier._verify_tui()  # noqa: SLF001

    common_arguments = [
        "--stub-agent",
        "--input",
        "<temporary-input>",
        "--exp-name",
        "installed-release-smoke",
        "--max-rounds",
        "1",
        "--local",
        "--no-skills",
        "--backend",
        "cpu",
        "--profiler",
        "none",
        "--runs-dir",
        "<temporary-runs>",
    ]
    valid_commands = [
        ["python", "-c", "raise SystemExit(0)"],
        ["python", "-c", "raise SystemExit(0)"],
    ]
    assert json.loads(observed.read_text()) == [
        {
            "normalized_argv": common_arguments,
            "runs_share_smoke_root": True,
            "input_files": ["OBJECTIVE.md", "vibesys.input.toml"],
            "manifest_commands": valid_commands,
            "ttys": [True, True, True],
        },
        {
            "normalized_argv": ["--headless", *common_arguments],
            "runs_share_smoke_root": True,
            "input_files": ["OBJECTIVE.md", "vibesys.input.toml"],
            "manifest_commands": valid_commands,
        },
    ]


def test_interactive_smoke_rejects_clean_exit_before_protocol_exchange(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_installed_cli.py"
    fake_cli.write_text("raise SystemExit(0)\n")

    with pytest.raises(verifier.InstalledReleaseError, match="control-protocol marker"):
        verifier.run_interactive_tui_smoke(
            [sys.executable, str(fake_cli)],
            env=os.environ.copy(),
            runtime_root=tmp_path,
            timeout=5,
        )


def test_headless_smoke_rejects_mutable_run_tree_under_sys_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tmp_path / "isolated-prefix"
    prefix.mkdir()
    fake_cli = tmp_path / "fake_installed_cli.py"
    fake_cli.write_text(
        """\
import os
from pathlib import Path

prefix = Path(os.environ["VIBESYS_TEST_PREFIX"])
(prefix / "lib" / "exp_env" / "unexpected-run").mkdir(parents=True)
arguments = os.sys.argv[1:]
runs_root = Path(arguments[arguments.index("--runs-dir") + 1])
(runs_root / "20260811-120000-installed-release-smoke").mkdir(parents=True)
"""
    )
    monkeypatch.setattr(verifier.sys, "prefix", str(prefix))
    environment = {**os.environ, "VIBESYS_TEST_PREFIX": str(prefix)}

    with pytest.raises(verifier.InstalledReleaseError, match="installation prefix"):
        verifier.run_headless_stub_smoke(
            [sys.executable, str(fake_cli)],
            env=environment,
            runtime_root=tmp_path,
            timeout=5,
        )


@pytest.mark.parametrize("run_count", [0, 2])
def test_headless_smoke_requires_exactly_one_run_in_selected_collection(
    tmp_path: Path,
    run_count: int,
) -> None:
    fake_cli = tmp_path / "fake_installed_cli.py"
    fake_cli.write_text(
        """\
import os
from pathlib import Path

arguments = os.sys.argv[1:]
runs_root = Path(arguments[arguments.index("--runs-dir") + 1])
for index in range(int(os.environ["VIBESYS_TEST_RUN_COUNT"])):
    (runs_root / f"20260811-12000{index}-installed-release-smoke").mkdir(parents=True)
"""
    )
    environment = {**os.environ, "VIBESYS_TEST_RUN_COUNT": str(run_count)}

    with pytest.raises(verifier.InstalledReleaseError, match="exactly one run"):
        verifier.run_headless_stub_smoke(
            [sys.executable, str(fake_cli)],
            env=environment,
            runtime_root=tmp_path,
            timeout=5,
        )
