"""Tests for the ``vibesys`` console entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from vibesys import cli


def _make_source_checkout(
    tmp_path: Path,
    *,
    project_name: str = "vibesys",
    tui_name: str = "@vibesys/tui",
) -> Path:
    cli_file = tmp_path / "src" / "vibesys" / "cli.py"
    cli_file.parent.mkdir(parents=True)
    cli_file.write_text("# fixture\n")
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "{project_name}"\n')
    package_json = tmp_path / "clients" / "tui" / "package.json"
    package_json.parent.mkdir(parents=True)
    package_json.write_text(f'{{"name": "{tui_name}"}}\n')
    return cli_file


def _make_bundle(tmp_path):  # noqa: ANN001, ANN202  # tracked: #288
    tui = tmp_path / "_tui"
    runtime = tui / "bin" / "bun"
    launcher = tui / "app" / "dist" / "launcher.js"
    runtime.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    runtime.write_text("#!/bin/sh\n")
    runtime.chmod(0o755)
    launcher.write_text("// launcher\n")
    return cli.BundledTui(root=tui, runtime=runtime, launcher=launcher)


def _force_interactive(monkeypatch):  # noqa: ANN001, ANN202  # tracked: #288
    """Make `_headless_requested` return False regardless of the test's TTY."""
    monkeypatch.setattr(cli, "_headless_requested", lambda _args: False)


def test_bundled_tui_none_in_source_checkout():  # noqa: ANN201
    # The source tree ships no built _tui, so resolution returns None here.
    assert cli.bundled_tui() is None


def test_headless_requested_detects_flag_and_validate():  # noqa: ANN201  # tracked: #288
    assert cli._headless_requested(["--headless", "--input", "x"]) is True  # noqa: SLF001  # tracked: #288
    assert cli._headless_requested(["validate", "bundle"]) is True  # noqa: SLF001  # tracked: #288
    assert cli._headless_requested(["--help"]) is True  # noqa: SLF001  # tracked: #288
    assert cli._headless_requested(["-h"]) is True  # noqa: SLF001  # tracked: #288


def test_headless_requested_when_not_a_tty(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    assert cli._headless_requested(["--input", "x"]) is True  # noqa: SLF001  # tracked: #288


def test_headless_flag_runs_engine_subprocess(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    captured: dict[str, object] = {}

    def _call(cmd, env=None):  # noqa: ANN001, ANN202, ARG001  # tracked: #288
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _call)

    rc = cli.main(["--headless", "--input", "bundle", "--local"])

    assert rc == 0
    assert captured["cmd"] == [
        sys.executable,
        "-m",
        "vibesys",
        "--headless",
        "--input",
        "bundle",
        "--local",
    ]


def test_interactive_execs_launcher_with_python_env(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    bundle = _make_bundle(tmp_path)
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: bundle)

    captured: dict[str, object] = {}

    def _call(cmd, env=None):  # noqa: ANN001, ANN202  # tracked: #288
        captured["cmd"] = cmd
        captured["env"] = env
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _call)

    with patch("shutil.which", side_effect=AssertionError("must not search system runtimes")):
        rc = cli.main(["--input", "bundle", "--local"])

    assert rc == 0
    assert captured["cmd"] == [
        str(bundle.runtime),
        str(bundle.launcher),
        "--input",
        "bundle",
        "--local",
    ]
    assert captured["env"]["VIBESYS_PYTHON"] == sys.executable  # pyright: ignore[reportIndexIssue]
    assert captured["env"]["VIBESYS_TUI_RUNTIME"] == str(bundle.runtime)  # pyright: ignore[reportIndexIssue]
    assert captured["env"]["BUN_CONFIG_SKIP_INSTALL_PACKAGES"] == "1"  # pyright: ignore[reportIndexIssue]


def test_interactive_with_non_executable_bundled_runtime_errors(  # noqa: ANN201
    monkeypatch,  # noqa: ANN001
    tmp_path,  # noqa: ANN001
    capsys,  # noqa: ANN001
):
    _force_interactive(monkeypatch)
    bundle = _make_bundle(tmp_path)
    bundle.runtime.chmod(0o644)
    monkeypatch.setattr(cli, "bundled_tui", lambda: bundle)

    with patch("shutil.which", side_effect=AssertionError("must not search system runtimes")):
        assert cli.main([]) == 1
    error = capsys.readouterr().err
    assert "bundled Bun runtime" in error
    assert "--project /path/to/repository --task TASK" in error


def test_no_bundle_no_checkout_falls_back_to_headless(monkeypatch, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: None)
    captured: dict[str, object] = {}

    def _call(cmd, env=None):  # noqa: ANN001, ANN202, ARG001  # tracked: #288
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _call)

    rc = cli.main(["--input", "bundle"])

    assert rc == 0
    assert captured["cmd"] == [sys.executable, "-m", "vibesys", "--input", "bundle"]
    assert "no source checkout" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Tier 2: build and run the TUI from a source checkout (subsumes ./vs)
# ---------------------------------------------------------------------------


def test_source_checkout_root_finds_this_repo():  # noqa: ANN201  # tracked: #288
    root = cli.source_checkout_root()
    assert root is not None
    assert (root / "clients" / "tui" / "package.json").is_file()


def test_source_checkout_builds_and_runs_launcher_from_callers_directory(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_bun_executable", lambda: Path("/usr/bin/bun"))
    monkeypatch.setattr(cli, "_node_executable", lambda: Path("/usr/bin/node"))
    monkeypatch.setattr(cli, "_node_major", lambda _node: 20)
    build_calls: list[bool] = []
    monkeypatch.setattr(cli, "_needs_rebuild", lambda _root: True)
    monkeypatch.setattr(
        cli, "_ensure_source_tui_built", lambda _root: build_calls.append(True) or True
    )

    captured: dict[str, object] = {}

    def _call(cmd, cwd=None, env=None):  # noqa: ANN001, ANN202  # tracked: #288
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _call)

    rc = cli.main(["--input", "bundle", "--local"])

    assert rc == 0
    assert build_calls == [True]  # a stale checkout was rebuilt
    launcher = tmp_path / "clients" / "tui" / "dist" / "launcher.js"
    assert captured["cmd"] == ["/usr/bin/node", str(launcher), "--input", "bundle", "--local"]
    assert captured["cwd"] is None
    assert captured["env"]["VIBESYS_PYTHON"] == sys.executable  # pyright: ignore[reportIndexIssue]


def test_source_checkout_skips_build_when_fresh(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_bun_executable", lambda: Path("/usr/bin/bun"))
    monkeypatch.setattr(cli, "_node_executable", lambda: Path("/usr/bin/node"))
    monkeypatch.setattr(cli, "_node_major", lambda _node: 22)
    monkeypatch.setattr(cli, "_needs_rebuild", lambda _root: False)

    message = "must not rebuild a fresh checkout"

    def _boom(_root):  # noqa: ANN001, ANN202  # tracked: #288
        raise AssertionError(message)

    monkeypatch.setattr(cli, "_ensure_source_tui_built", _boom)
    monkeypatch.setattr(cli.subprocess, "call", lambda *a, **k: 0)  # noqa: ARG005  # tracked: #288

    assert cli.main([]) == 0


def test_source_checkout_missing_bun_errors(monkeypatch, tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_bun_executable", lambda: None)

    assert cli.main([]) == 1
    assert "Bun is required" in capsys.readouterr().err


def test_source_checkout_old_node_errors(monkeypatch, tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_bun_executable", lambda: Path("/usr/bin/bun"))
    monkeypatch.setattr(cli, "_node_executable", lambda: Path("/usr/bin/node"))
    monkeypatch.setattr(cli, "_node_major", lambda _node: 18)

    assert cli.main([]) == 1
    assert "Node.js 20+" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Tier-2 helper units (toolchain resolution, staleness, build)
# ---------------------------------------------------------------------------


def test_pnpm_argv_prefers_pnpm_then_corepack_then_none(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/usr/bin/pnpm" if n == "pnpm" else None)
    assert cli._pnpm_argv() == ["/usr/bin/pnpm"]  # noqa: SLF001  # tracked: #288
    monkeypatch.setattr(
        cli.shutil, "which", lambda n: "/usr/bin/corepack" if n == "corepack" else None
    )
    assert cli._pnpm_argv() == ["/usr/bin/corepack", "pnpm"]  # noqa: SLF001  # tracked: #288
    monkeypatch.setattr(cli.shutil, "which", lambda _n: None)
    assert cli._pnpm_argv() is None  # noqa: SLF001  # tracked: #288


def test_bun_executable_path_then_home_fallback_then_none(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/usr/bin/bun" if n == "bun" else None)
    assert cli._bun_executable() == Path("/usr/bin/bun")  # noqa: SLF001  # tracked: #288

    monkeypatch.setattr(cli.shutil, "which", lambda _n: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli._bun_executable() is None  # noqa: SLF001  # tracked: #288

    bun = tmp_path / ".bun" / "bin" / "bun"
    bun.parent.mkdir(parents=True)
    bun.write_text("#!/bin/sh\n")
    bun.chmod(0o755)
    assert cli._bun_executable() == bun  # noqa: SLF001  # tracked: #288


def test_node_executable(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/usr/bin/node" if n == "node" else None)
    assert cli._node_executable() == Path("/usr/bin/node")  # noqa: SLF001  # tracked: #288
    monkeypatch.setattr(cli.shutil, "which", lambda _n: None)
    assert cli._node_executable() is None  # noqa: SLF001  # tracked: #288


def test_node_major_parses_version_and_handles_errors(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    import subprocess as sp  # noqa: PLC0415  # tracked: #288
    from types import SimpleNamespace  # noqa: PLC0415  # tracked: #288

    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="v20.3.1\n"),  # noqa: ARG005  # tracked: #288
    )
    assert cli._node_major(Path("/usr/bin/node")) == 20  # noqa: SLF001  # tracked: #288

    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="weird\n"),  # noqa: ARG005  # tracked: #288
    )
    assert cli._node_major(Path("/usr/bin/node")) is None  # noqa: SLF001  # tracked: #288

    def _raise(*_a, **_k):  # noqa: ANN002, ANN003, ANN202  # tracked: #288
        raise sp.CalledProcessError(1, "node")

    monkeypatch.setattr(cli.subprocess, "run", _raise)
    assert cli._node_major(Path("/usr/bin/node")) is None  # noqa: SLF001  # tracked: #288


def _make_checkout(tmp_path):  # noqa: ANN001, ANN202  # tracked: #288
    dist = tmp_path / "clients" / "tui" / "dist"
    src = tmp_path / "clients" / "tui" / "src"
    dist.mkdir(parents=True)
    src.mkdir(parents=True)
    (dist / "index.js").write_text("// index\n")
    (dist / "launcher.js").write_text("// launcher\n")
    (src / "app.ts").write_text("// src\n")
    return tmp_path


def _set_mtime(path, when):  # noqa: ANN001, ANN202  # tracked: #288
    import os  # noqa: PLC0415  # tracked: #288

    os.utime(path, (when, when))


def test_needs_rebuild_states(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    root = _make_checkout(tmp_path)
    dist = root / "clients" / "tui" / "dist"
    src_file = root / "clients" / "tui" / "src" / "app.ts"

    # Fresh: dist newer than sources -> no rebuild.
    _set_mtime(src_file, 1000)
    _set_mtime(dist / "index.js", 2000)
    _set_mtime(dist / "launcher.js", 2000)
    assert cli._needs_rebuild(root) is False  # noqa: SLF001  # tracked: #288

    # Stale: a source is newer than the built entry -> rebuild.
    _set_mtime(src_file, 3000)
    assert cli._needs_rebuild(root) is True  # noqa: SLF001  # tracked: #288

    # Missing build output -> rebuild.
    (dist / "index.js").unlink()
    assert cli._needs_rebuild(root) is True  # noqa: SLF001  # tracked: #288


def test_ensure_built_requires_pnpm(monkeypatch, tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    monkeypatch.setattr(cli, "_pnpm_argv", lambda: None)
    assert cli._ensure_source_tui_built(tmp_path) is False  # noqa: SLF001  # tracked: #288
    assert "pnpm is required" in capsys.readouterr().err


def test_ensure_built_runs_pnpm_steps(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    from types import SimpleNamespace  # noqa: PLC0415  # tracked: #288

    monkeypatch.setattr(cli, "_pnpm_argv", lambda: ["/usr/bin/pnpm"])
    calls: list[list[str]] = []

    def _run(cmd, cwd=None, capture_output=False, text=False, check=False):  # noqa: ANN001, ANN202, ARG001, FBT002  # tracked: #288
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", _run)
    assert cli._ensure_source_tui_built(tmp_path) is True  # noqa: SLF001  # tracked: #288
    assert [c[1] for c in calls] == ["install", "--dir", "--dir"]


def test_ensure_built_reports_failure(monkeypatch, tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    from types import SimpleNamespace  # noqa: PLC0415  # tracked: #288

    monkeypatch.setattr(cli, "_pnpm_argv", lambda: ["/usr/bin/pnpm"])

    def _run(cmd, cwd=None, capture_output=False, text=False, check=False):  # noqa: ANN001, ANN202, ARG001, FBT002  # tracked: #288
        return SimpleNamespace(returncode=1, stdout="boom-out", stderr="boom-err")

    monkeypatch.setattr(cli.subprocess, "run", _run)
    assert cli._ensure_source_tui_built(tmp_path) is False  # noqa: SLF001  # tracked: #288
    err = capsys.readouterr().err
    assert "failed to build" in err
    assert "boom-err" in err


def test_bundled_tui_missing_launcher_errors(monkeypatch, tmp_path, capsys):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    bundle = _make_bundle(tmp_path)
    bundle.launcher.unlink()  # runtime present + executable, launcher gone
    monkeypatch.setattr(cli, "bundled_tui", lambda: bundle)

    with patch("shutil.which", side_effect=AssertionError("must not search system runtimes")):
        assert cli.main([]) == 1
    assert "bundled Bun runtime" in capsys.readouterr().err


def test_bundled_tui_resolves_when_staged(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    (tmp_path / "_tui").mkdir()
    monkeypatch.setattr(cli, "files", lambda _pkg: tmp_path)
    bundle = cli.bundled_tui()
    assert bundle is not None
    assert bundle.root == tmp_path / "_tui"
    assert bundle.runtime == tmp_path / "_tui" / "bin" / "bun"
    assert bundle.launcher == tmp_path / "_tui" / "app" / "dist" / "launcher.js"


def test_source_checkout_root_ignores_unrelated_current_directory(monkeypatch, tmp_path):  # noqa: ANN001, ANN201
    lookalike = tmp_path / "lookalike"
    _make_source_checkout(lookalike)
    nested = lookalike / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    monkeypatch.setattr(cli, "__file__", tmp_path / "site-packages" / "vibesys" / "cli.py")

    assert cli.source_checkout_root() is None


def test_source_checkout_root_rejects_wrong_python_project_name(monkeypatch, tmp_path):  # noqa: ANN001, ANN201
    cli_file = _make_source_checkout(tmp_path, project_name="not-vibesys")
    monkeypatch.setattr(cli, "__file__", cli_file)

    assert cli.source_checkout_root() is None


def test_source_checkout_root_rejects_nonstandard_json_constant(monkeypatch, tmp_path):  # noqa: ANN001, ANN201
    cli_file = _make_source_checkout(tmp_path)
    package_json = tmp_path / "clients" / "tui" / "package.json"
    package_json.write_text('{"name":"@vibesys/tui","x":NaN}\n')
    monkeypatch.setattr(cli, "__file__", cli_file)

    assert cli.source_checkout_root() is None


def test_needs_rebuild_on_watched_config_file(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    root = _make_checkout(tmp_path)
    dist = root / "clients" / "tui" / "dist"
    pkg = root / "clients" / "tui" / "package.json"
    pkg.write_text("{}\n")
    _set_mtime(root / "clients" / "tui" / "src" / "app.ts", 1000)
    _set_mtime(dist / "index.js", 2000)
    _set_mtime(dist / "launcher.js", 2000)
    _set_mtime(pkg, 3000)  # a watched config file newer than the build
    assert cli._needs_rebuild(root) is True  # noqa: SLF001  # tracked: #288


def test_source_checkout_build_failure_returns_one(monkeypatch, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    _force_interactive(monkeypatch)
    monkeypatch.setattr(cli, "bundled_tui", lambda: None)
    monkeypatch.setattr(cli, "source_checkout_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_bun_executable", lambda: Path("/usr/bin/bun"))
    monkeypatch.setattr(cli, "_node_executable", lambda: Path("/usr/bin/node"))
    monkeypatch.setattr(cli, "_node_major", lambda _node: 20)
    monkeypatch.setattr(cli, "_needs_rebuild", lambda _root: True)
    monkeypatch.setattr(cli, "_ensure_source_tui_built", lambda _root: False)

    assert cli.main([]) == 1
