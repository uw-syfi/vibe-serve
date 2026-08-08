"""Snapshot resilience: a workspace file the snapshotting user cannot read
(e.g. a root-written mode-600 profiler artifact on the Docker path) must not
abort `git add -A` / the whole run. Such files are excluded, not fatal.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from vibesys.run import GitTracker


def _git(ws, *args):  # noqa: ANN001, ANN002, ANN202  # tracked: #288
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)  # noqa: S603, S607  # tracked: #288


def _make_tracker(ws, excluded_dirs=()):  # noqa: ANN001, ANN202  # tracked: #288
    return GitTracker(ws, log=lambda *a, **k: None, excluded_dirs=excluded_dirs)  # noqa: ARG005  # tracked: #288


def test_workspace_gitignore_excludes_compiled_artifacts(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(tmp_path, excluded_dirs={".git", "__pycache__", "_mounts", "target"})
    gi = tracker._workspace_gitignore()  # noqa: SLF001  # tracked: #288
    # accelerator artifacts an agent might drop into the workspace
    for pat in ("*.neff", "*.ntff", "neuron-compile-cache/"):
        assert pat in gi
    # still excludes the standard dirs
    assert ".git" in gi and "_mounts" in gi and "target" in gi  # noqa: PT018  # tracked: #288


def test_unreadable_from_stderr_parses_git_output():  # noqa: ANN201  # tracked: #288
    stderr = (
        'error: open("system_profile.json"): Permission denied\n'
        "error: unable to index file 'sub/ntrace.pb'\n"
        "fatal: adding files failed\n"
    )
    assert GitTracker._unreadable_from_stderr(stderr) == [  # noqa: SLF001  # tracked: #288
        "system_profile.json",
        "sub/ntrace.pb",
    ]


def test_collect_unreadable_finds_mode000_file(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ok.txt").write_text("hi")
    secret = ws / "secret.bin"
    secret.write_text("x")
    os.chmod(secret, 0o000)  # noqa: PTH101  # tracked: #288
    try:
        tracker = _make_tracker(ws)
        assert tracker._collect_unreadable() == ["secret.bin"]  # noqa: SLF001  # tracked: #288
    finally:
        os.chmod(secret, 0o644)  # let pytest clean up  # noqa: PTH101  # tracked: #288


def test_collect_unreadable_does_not_enter_ignored_runtime_trees(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    ws = tmp_path / "ws"
    ignored = ws / ".venv" / "lib"
    ignored.mkdir(parents=True)
    (ignored / "large-package.so").write_text("cached")
    source = ws / "src"
    source.mkdir()
    (source / "engine.py").write_text("pass\n")

    checked: list[str] = []
    real_access = os.access

    def recording_access(path, mode):  # noqa: ANN001, ANN202  # tracked: #288
        checked.append(os.fspath(path))
        return real_access(path, mode)

    monkeypatch.setattr(os, "access", recording_access)
    tracker = _make_tracker(ws, excluded_dirs={".venv"})

    assert tracker._collect_unreadable() == []  # noqa: SLF001  # tracked: #288
    assert any(path.endswith("src/engine.py") for path in checked)
    assert all(".venv" not in path for path in checked)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read mode-000 files")
def test_git_add_all_excludes_unreadable_and_succeeds(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init")
    (ws / "code.py").write_text("print('hi')\n")
    secret = ws / "system_profile.json"
    secret.write_text("{}")
    os.chmod(  # noqa: PTH101  # tracked: #288
        secret, 0o600
    )  # owner-read, but pytest runs as the owner...  # noqa: PTH101, RUF100  # tracked: #288
    os.chmod(secret, 0o000)  # ...so make it truly unreadable  # noqa: PTH101  # tracked: #288

    tracker = _make_tracker(ws)
    info_exclude = ws / ".git" / "info" / "exclude"
    original_info_exclude = info_exclude.read_text()
    os.chmod(info_exclude, 0o444)  # noqa: PTH101  # tracked: #288
    try:
        # Plain `git add -A` would exit 128 here; the resilient path must not.
        tracker._add_all()  # noqa: SLF001  # tracked: #288
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],  # noqa: S607  # tracked: #288
            cwd=ws,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert "code.py" in staged
        assert "system_profile.json" not in staged
        # The offender is recorded outside the worktree; framework snapshots
        # do not depend on a sandbox-owned `.git/info/exclude` being writable.
        exclude = tracker._exclude_file.read_text()  # noqa: SLF001  # tracked: #288
        assert "system_profile.json" in exclude
        assert info_exclude.read_text() == original_info_exclude
    finally:
        os.chmod(info_exclude, 0o644)  # noqa: PTH101  # tracked: #288
        os.chmod(secret, 0o644)  # noqa: PTH101  # tracked: #288


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read mode-000 files")
def test_git_add_all_excludes_unreadable_when_workspace_below_repo_root(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """Experiment layout: the repo root is the experiment dir, the tracked
    workspace lives below it. Exclusions must be rooted correctly while the
    framework keeps them outside the sandbox-owned repository metadata."""
    exp = tmp_path / "exp"
    ws = exp / "workspace"
    ws.mkdir(parents=True)
    _git(exp, "init")
    (ws / "code.py").write_text("print('hi')\n")
    model = ws / "model"
    model.mkdir()
    secret = model / "model.safetensors"
    secret.write_text("x")
    os.chmod(secret, 0o000)  # noqa: PTH101  # tracked: #288

    tracker = _make_tracker(ws)
    try:
        tracker._add_all()  # noqa: SLF001  # tracked: #288
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],  # noqa: S607  # tracked: #288
            cwd=exp,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert "workspace/code.py" in staged
        assert "workspace/model/model.safetensors" not in staged
        # Recorded in framework-owned state, anchored to the repo toplevel.
        exclude = tracker._exclude_file.read_text()  # noqa: SLF001  # tracked: #288
        assert "/workspace/model/model.safetensors" in exclude
        assert (
            "/workspace/model/model.safetensors"
            not in (exp / ".git" / "info" / "exclude").read_text()
        )
        # No spurious git dir invented inside the workspace.
        assert not (ws / ".git").exists()
    finally:
        os.chmod(secret, 0o644)  # noqa: PTH101  # tracked: #288


def test_agent_created_nested_repo_does_not_hijack_snapshots(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """An agent running `git init`/`uv init` inside the workspace mid-run must
    not capture later snapshots (or fail them with dubious-ownership errors):
    the tracker stays pinned to the repository resolved at init time."""
    exp = tmp_path / "exp"
    ws = exp / "workspace"
    ws.mkdir(parents=True)
    _git(exp, "init")
    (ws / "code.py").write_text("v1\n")

    tracker = _make_tracker(ws)
    tracker.init(existing=False)

    # Agent side effect: a nested repo appears inside the workspace.
    _git(ws, "init")
    (ws / "code.py").write_text("v2\n")
    tracker.snapshot("round-1")

    exp_log = subprocess.run(  # noqa: S603  # tracked: #288
        ["git", "--git-dir", str(exp / ".git"), "log", "--oneline"],  # noqa: S607  # tracked: #288
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "round-1" in exp_log
    nested_log = subprocess.run(  # noqa: PLW1510, S603  # tracked: #288
        ["git", "--git-dir", str(ws / ".git"), "log", "--oneline"],  # noqa: S607  # tracked: #288
        capture_output=True,
        text=True,
    ).stdout
    assert "round-1" not in nested_log
