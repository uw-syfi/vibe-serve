"""Snapshot resilience: a workspace file the snapshotting user cannot read
(e.g. a root-written mode-600 profiler artifact on the Docker path) must not
abort `git add -A` / the whole run. Such files are excluded, not fatal.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from vibesys.run import GitTracker


def _git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)


def _make_tracker(ws, excluded_dirs=()):
    return GitTracker(ws, log=lambda *a, **k: None, excluded_dirs=excluded_dirs)


def test_workspace_gitignore_excludes_compiled_artifacts(tmp_path):
    tracker = _make_tracker(tmp_path, excluded_dirs={".git", "__pycache__", "_mounts", "target"})
    gi = tracker._workspace_gitignore()
    # accelerator artifacts an agent might drop into the workspace
    for pat in ("*.neff", "*.ntff", "neuron-compile-cache/"):
        assert pat in gi
    # still excludes the standard dirs
    assert ".git" in gi and "_mounts" in gi and "target" in gi


def test_unreadable_from_stderr_parses_git_output():
    stderr = (
        'error: open("system_profile.json"): Permission denied\n'
        "error: unable to index file 'sub/ntrace.pb'\n"
        "fatal: adding files failed\n"
    )
    assert GitTracker._unreadable_from_stderr(stderr) == [
        "system_profile.json",
        "sub/ntrace.pb",
    ]


def test_collect_unreadable_finds_mode000_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ok.txt").write_text("hi")
    secret = ws / "secret.bin"
    secret.write_text("x")
    os.chmod(secret, 0o000)
    try:
        tracker = _make_tracker(ws)
        assert tracker._collect_unreadable() == ["secret.bin"]
    finally:
        os.chmod(secret, 0o644)  # let pytest clean up


def test_collect_unreadable_does_not_enter_ignored_runtime_trees(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ignored = ws / ".venv" / "lib"
    ignored.mkdir(parents=True)
    (ignored / "large-package.so").write_text("cached")
    source = ws / "src"
    source.mkdir()
    (source / "engine.py").write_text("pass\n")

    checked: list[str] = []
    real_access = os.access

    def recording_access(path, mode):
        checked.append(os.fspath(path))
        return real_access(path, mode)

    monkeypatch.setattr(os, "access", recording_access)
    tracker = _make_tracker(ws, excluded_dirs={".venv"})

    assert tracker._collect_unreadable() == []
    assert any(path.endswith("src/engine.py") for path in checked)
    assert all(".venv" not in path for path in checked)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read mode-000 files")
def test_git_add_all_excludes_unreadable_and_succeeds(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init")
    (ws / "code.py").write_text("print('hi')\n")
    secret = ws / "system_profile.json"
    secret.write_text("{}")
    os.chmod(secret, 0o600)  # owner-read, but pytest runs as the owner...
    os.chmod(secret, 0o000)  # ...so make it truly unreadable

    tracker = _make_tracker(ws)
    info_exclude = ws / ".git" / "info" / "exclude"
    original_info_exclude = info_exclude.read_text()
    os.chmod(info_exclude, 0o444)
    try:
        # Plain `git add -A` would exit 128 here; the resilient path must not.
        tracker._add_all()
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=ws,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert "code.py" in staged
        assert "system_profile.json" not in staged
        # The offender is recorded outside the worktree; framework snapshots
        # do not depend on a sandbox-owned `.git/info/exclude` being writable.
        exclude = tracker._exclude_file.read_text()
        assert "system_profile.json" in exclude
        assert info_exclude.read_text() == original_info_exclude
    finally:
        os.chmod(info_exclude, 0o644)
        os.chmod(secret, 0o644)
