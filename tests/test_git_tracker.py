"""GitTracker unit tests against real temporary git repos (no mocks)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vibesys.run import GitTracker
from vibesys.run.git_tracker import GitTrackingMode

_EXCLUDED = {".git", "__pycache__", "target"}


def _make_tracker(ws, logs=None):  # noqa: ANN001, ANN202  # tracked: #288
    log = logs.append if logs is not None else (lambda _msg: None)
    return GitTracker(ws, log=log, excluded_dirs=_EXCLUDED)


def _git_stdout(ws, *args) -> str:  # noqa: ANN001, ANN002  # tracked: #288
    return subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True, text=True).stdout  # noqa: S603, S607  # tracked: #288


def _git_run(ws, *args):  # noqa: ANN001, ANN002, ANN202
    return subprocess.run(["git", *args], cwd=ws, check=True)  # noqa: S603, S607


def _make_project_tracker(ws, run_id="test-run", logs=None):  # noqa: ANN001, ANN202
    log = logs.append if logs is not None else (lambda _msg: None)
    return GitTracker(
        ws,
        log=log,
        excluded_dirs=_EXCLUDED,
        mode=GitTrackingMode.USER_PROJECT,
        run_id=run_id,
    )


def _init_repo_with_commit(project):  # noqa: ANN001, ANN202
    _git_run(project, "init", "-q", "-b", "main")
    _git_run(project, "add", "-A")
    _git_run(
        project,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        "baseline",
    )


@pytest.fixture
def ws(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("VALUE = 1\n")
    return ws


def test_init_creates_repo_gitignore_and_initial_commit(ws):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(ws)
    tracker.init(existing=False)

    assert (ws / ".git").is_dir()
    gitignore = (ws / ".gitignore").read_text()
    assert "target" in gitignore
    assert "*.neff" in gitignore
    assert "*.otlp.ndjson" in gitignore
    log = _git_stdout(ws, "log", "--format=%s")
    assert log.strip() == "initial: workspace setup"


def test_init_appends_to_existing_gitignore(ws):  # noqa: ANN001, ANN201  # tracked: #288
    (ws / ".gitignore").write_text("custom-entry")  # no trailing newline
    tracker = _make_tracker(ws)
    tracker.init(existing=False)

    gitignore = (ws / ".gitignore").read_text()
    assert gitignore.startswith("custom-entry\n")
    assert "*.neff" in gitignore


def test_init_uses_containing_experiment_repo_without_nesting(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    experiment = tmp_path / "experiment"
    workspace = experiment / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=experiment, check=True)  # noqa: S607  # tracked: #288

    tracker = _make_tracker(workspace)
    tracker.init(existing=False)

    assert not (workspace / ".git").exists()
    assert tracker.history_root == experiment.resolve()
    assert _git_stdout(workspace, "show", "--format=", "--name-only").strip().splitlines() == [
        "workspace/.gitignore",
        "workspace/main.py",
    ]


def test_snapshot_stays_bound_when_agent_creates_nested_repo(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    experiment = tmp_path / "experiment"
    workspace = experiment / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=experiment, check=True)  # noqa: S607  # tracked: #288

    tracker = _make_tracker(workspace)
    tracker.init(existing=False)
    initial_sha = tracker.current_sha()
    assert tracker._exclude_pattern("secret.bin") == "/workspace/secret.bin"  # noqa: SLF001  # tracked: #288
    assert tracker._exclude_pattern("workspace/secret.bin") == "/workspace/secret.bin"  # noqa: SLF001  # tracked: #288

    # Reproduce an isolated/root agent running plain `uv init`: a new `.git`
    # appears below the already-selected experiment repository.
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)  # noqa: S607  # tracked: #288
    (workspace / "main.py").write_text("VALUE = 2\n")
    tracker.snapshot("round 1")

    assert tracker.current_sha() != initial_sha
    assert _git_stdout(experiment, "log", "-1", "--format=%s").strip() == "round 1"
    nested_head = subprocess.run(  # noqa: PLW1510  # tracked: #288
        ["git", "rev-parse", "--verify", "HEAD"],  # noqa: S607  # tracked: #288
        cwd=workspace,
        capture_output=True,
    )
    assert nested_head.returncode != 0


def test_init_existing_requires_repo(ws):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(ws)
    with pytest.raises(ValueError, match="no git repository"):
        tracker.init(existing=True)

    tracker.init(existing=False)
    tracker.init(existing=True)  # now valid; must not re-init or commit
    log = _git_stdout(ws, "log", "--format=%s")
    assert log.strip() == "initial: workspace setup"


def test_snapshot_commits_changes_and_skips_clean_tree(ws):  # noqa: ANN001, ANN201  # tracked: #288
    logs: list[str] = []
    tracker = _make_tracker(ws, logs)
    tracker.init(existing=False)

    (ws / "main.py").write_text("VALUE = 2\n")
    tracker.snapshot("round 1")
    assert _git_stdout(ws, "log", "-1", "--format=%s").strip() == "round 1"

    tracker.snapshot("round 2")  # nothing changed — no commit, only a log line
    assert _git_stdout(ws, "log", "-1", "--format=%s").strip() == "round 1"
    assert any("no changes to commit for 'round 2'" in line for line in logs)


def test_current_sha_matches_head_and_is_none_without_repo(ws):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(ws)
    assert tracker.current_sha() is None  # no repo yet

    tracker.init(existing=False)
    assert tracker.current_sha() == _git_stdout(ws, "rev-parse", "HEAD").strip()


def test_pending_changes_reports_tracked_and_untracked_paths(ws):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(ws)
    tracker.init(existing=False)

    (ws / "main.py").write_text("VALUE = 2\n")
    (ws / "new.txt").write_text("new\n")

    assert tracker.pending_changes() == ["main.py", "new.txt"]


def test_pending_changes_are_relative_to_nested_workspace(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    experiment = tmp_path / "experiment"
    workspace = experiment / "workspace"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=experiment, check=True)  # noqa: S607  # tracked: #288
    (workspace / "main.py").write_text("VALUE = 1\n")
    tracker = _make_tracker(workspace)
    tracker.init(existing=False)

    (workspace / "main.py").write_text("VALUE = 2\n")
    (workspace / "new.txt").write_text("new\n")

    assert tracker.pending_changes() == ["main.py", "new.txt"]


def test_checkout_tree_restores_snapshot_without_moving_head(ws):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(ws)
    tracker.init(existing=False)
    first = tracker.current_sha()

    (ws / "main.py").write_text("VALUE = 2\n")
    (ws / "later.py").write_text("ONLY_IN_LATER_SNAPSHOT = True\n")
    tracker.snapshot("round 1")
    second = tracker.current_sha()

    assert tracker.checkout_tree(first) is True  # pyright: ignore[reportArgumentType]  # tracked: #297
    assert (ws / "main.py").read_text() == "VALUE = 1\n"
    assert not (ws / "later.py").exists()
    # HEAD stays put so the next commit lands as a new child commit.
    assert tracker.current_sha() == second


def test_checkout_tree_clean_removes_untracked_files(ws):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(ws)
    tracker.init(existing=False)
    first = tracker.current_sha()

    (ws / "leftover.txt").write_text("scratch\n")
    assert tracker.checkout_tree(first, clean=True) is True  # pyright: ignore[reportArgumentType]  # tracked: #297
    assert not (ws / "leftover.txt").exists()


def test_checkout_tree_clean_keeps_ignored_runtime_assets(ws):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = GitTracker(
        ws,
        log=lambda _msg: None,
        excluded_dirs={".git", ".venv", ".cache"},
    )
    tracker.init(existing=False)
    first = tracker.current_sha()
    assert first is not None

    environment = ws / ".venv" / "bin" / "python"
    environment.parent.mkdir(parents=True)
    environment.write_text("persistent interpreter\n")
    cache_entry = ws / ".cache" / "uv" / "wheel"
    cache_entry.parent.mkdir(parents=True)
    cache_entry.write_text("persistent package cache\n")
    (ws / "scratch.txt").write_text("remove me\n")

    assert tracker.checkout_tree(first, clean=True) is True
    assert environment.read_text() == "persistent interpreter\n"
    assert cache_entry.read_text() == "persistent package cache\n"
    assert not (ws / "scratch.txt").exists()


def test_checkout_tree_preserves_framework_memory_while_removing_later_code(ws):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(ws)
    tracker.init(existing=False)
    first = tracker.current_sha()

    (ws / "later.py").write_text("ONLY_IN_LATER_SNAPSHOT = True\n")
    progress = ws / "progress"
    progress.mkdir()
    (progress / "round-0002.md").write_text("# Round 2\n")
    tracker.snapshot("round 2")
    second = tracker.current_sha()
    (progress / "round-0003.md").write_text("# Round 3 in progress\n")

    assert tracker.checkout_tree(
        first,  # pyright: ignore[reportArgumentType]  # tracked: #297
        clean=True,
        preserve_paths=("progress",),
    )
    assert not (ws / "later.py").exists()
    assert (progress / "round-0002.md").read_text() == "# Round 2\n"
    assert (progress / "round-0003.md").read_text() == "# Round 3 in progress\n"
    assert tracker.current_sha() == second


def test_checkout_tree_returns_false_and_logs_on_bad_sha(ws):  # noqa: ANN001, ANN201  # tracked: #288
    logs: list[str] = []
    tracker = _make_tracker(ws, logs)
    tracker.init(existing=False)

    assert tracker.checkout_tree("0000000000000000000000000000000000000000") is False
    assert any("git tree restore 00000000 failed" in line for line in logs)


def test_trusted_input_changes_reports_committed_and_pending_edits(ws):  # noqa: ANN001, ANN201  # tracked: #288
    (ws / "accuracy_checker").mkdir()
    (ws / "accuracy_checker" / "checker.py").write_text("print('ok')\n")
    tracker = _make_tracker(ws)
    tracker.init(existing=False)

    # Non-evaluator edits are not reported, committed or not.
    (ws / "main.py").write_text("VALUE = 2\n")
    tracker.snapshot("round 1")
    assert tracker.trusted_input_changes() == []

    # Pending (uncommitted) evaluator edits are reported...
    (ws / "accuracy_checker" / "checker.py").write_text("print('forged')\n")
    assert tracker.trusted_input_changes() == ["accuracy_checker/checker.py"]

    # ...and stay reported once committed (diff against the root commit).
    tracker.snapshot("round 2")
    assert tracker.trusted_input_changes() == ["accuracy_checker/checker.py"]


def test_resume_can_authorize_immutable_trusted_input_baseline(ws):  # noqa: ANN001, ANN201  # tracked: #288
    (ws / "accuracy_checker").mkdir()
    checker = ws / "accuracy_checker" / "checker.py"
    checker.write_text("print('v1')\n")
    tracker = _make_tracker(ws)
    tracker.init(existing=False)

    checker.write_text("print('operator-authorized v2')\n")
    tracker.snapshot("refresh trusted evaluator")
    refresh_commit = tracker.current_sha()
    assert refresh_commit is not None

    resumed = _make_tracker(ws)
    resumed.init(existing=True, trusted_input_baseline=refresh_commit)
    assert resumed.trusted_input_changes() == []

    checker.write_text("print('agent tampering')\n")
    assert resumed.trusted_input_changes() == ["accuracy_checker/checker.py"]
    resumed.snapshot("later committed tampering")
    assert resumed.trusted_input_changes() == ["accuracy_checker/checker.py"]


def test_resume_rejects_invalid_trusted_input_baseline(ws):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(ws)
    tracker.init(existing=False)

    resumed = _make_tracker(ws)
    with pytest.raises(ValueError, match="is not a commit"):
        resumed.init(existing=True, trusted_input_baseline="not-a-revision")


def test_run_is_a_public_escape_hatch(ws):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(ws)
    tracker.init(existing=False)

    result = tracker.run(["git", "status", "--porcelain"], check=True)
    assert result.returncode == 0


def test_add_worktree_materializes_commit_and_shares_object_store(ws, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    tracker = _make_tracker(ws)
    tracker.init(existing=False)
    base_sha = tracker.current_sha()
    assert base_sha is not None

    # Advance the main tree so the worktree must reflect the OLD commit, not HEAD.
    (ws / "main.py").write_text("VALUE = 99\n")
    tracker.snapshot("advance")

    wt = tmp_path / "candidates" / "g1c1"
    tracker.add_worktree(wt, base_sha)

    # Worktree holds the parent commit's content, isolated from the main tree.
    assert (wt / "main.py").read_text() == "VALUE = 1\n"
    assert (ws / "main.py").read_text() == "VALUE = 99\n"

    # A commit in the worktree lands in the SHARED object store: the main repo
    # can resolve it by sha.
    wt_tracker = _make_tracker(wt)
    (wt / "main.py").write_text("VALUE = 7\n")
    wt_tracker.snapshot("child")
    child_sha = wt_tracker.current_sha()
    assert child_sha is not None and child_sha != base_sha  # noqa: PT018  # tracked: #288
    # `git cat-file -e <sha>` in the MAIN repo succeeds → object is shared.
    assert tracker.run(["git", "cat-file", "-e", child_sha], check=False).returncode == 0

    # Untracked files must not block cleanup: the directory is always deleted.
    (wt / "scratch.log").write_text("editor leftover\n")
    tracker.remove_worktree(wt)
    assert not wt.exists()
    # The child commit object survives worktree removal (shared store).
    assert tracker.run(["git", "cat-file", "-e", child_sha], check=False).returncode == 0


def test_remove_worktree_deletes_orphaned_directory(ws, tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """If `git worktree remove` leaves the directory behind (as observed when a
    file is still held open by a just-stopped editor container), the explicit
    recursive delete still clears it."""
    import shutil  # noqa: PLC0415  # tracked: #288

    tracker = _make_tracker(ws)
    tracker.init(existing=False)
    base_sha = tracker.current_sha()
    assert base_sha is not None

    wt = tmp_path / "candidates" / "g2c1"
    tracker.add_worktree(wt, base_sha)
    assert wt.exists()

    # Orphan the worktree: drop git's admin registration but leave the tree on
    # disk. `git worktree remove` then no-ops ("not a working tree"), so only
    # the rmtree fallback can clear the leftover directory.
    shutil.rmtree(ws / ".git" / "worktrees")
    tracker.remove_worktree(wt)
    assert not wt.exists()


def test_project_mode_initializes_baseline_branch_and_local_excludes(ws):  # noqa: ANN001, ANN201
    (ws / ".env").write_text("TOKEN=secret\n")
    (ws / "agent.toml").write_text("provider = 'local'\n")
    local_state = ws / ".vs" / "local" / "state.json"
    local_state.parent.mkdir(parents=True)
    local_state.write_text("{}\n")

    tracker = _make_project_tracker(ws, run_id="run-001")
    tracker.init(existing=False)

    assert not (ws / ".gitignore").exists()
    assert _git_stdout(ws, "branch", "--show-current").strip() == "vibesys/run-001"
    assert _git_stdout(ws, "log", "--format=%s").strip() == "initial: project baseline"
    assert _git_stdout(ws, "show", "--format=", "--name-only").splitlines() == ["main.py"]
    exclude_file = Path(_git_stdout(ws, "rev-parse", "--git-path", "info/exclude").strip())
    if not exclude_file.is_absolute():
        exclude_file = ws / exclude_file
    excludes = exclude_file.read_text().splitlines()
    assert excludes.count("/.vs/local/") == 1
    assert excludes.count("/.env") == 1
    assert excludes.count("/agent.toml") == 1
    assert "__pycache__/" in excludes
    assert "target/" in excludes

    # Rebinding the same run is idempotent and leaves in-flight project edits
    # alone. It also switches back from another clean branch.
    _git_run(ws, "switch", "main")
    resumed = _make_project_tracker(ws, run_id="run-001")
    resumed.init(existing=True)
    assert _git_stdout(ws, "branch", "--show-current").strip() == "vibesys/run-001"
    assert exclude_file.read_text().splitlines().count("/.vs/local/") == 1


def test_project_mode_branches_from_clean_existing_repository(ws):  # noqa: ANN001, ANN201
    _init_repo_with_commit(ws)
    baseline = _git_stdout(ws, "rev-parse", "HEAD").strip()
    original_gitignore = "user-owned\n"
    (ws / ".gitignore").write_text(original_gitignore)
    _git_run(ws, "add", ".gitignore")
    _git_run(
        ws,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        "gitignore",
    )
    baseline = _git_stdout(ws, "rev-parse", "HEAD").strip()

    tracker = _make_project_tracker(ws, run_id="existing")
    tracker.init(existing=False)

    assert tracker.current_sha() == baseline
    assert tracker.trusted_input_baseline == baseline
    assert _git_stdout(ws, "branch", "--show-current").strip() == "vibesys/existing"
    assert (ws / ".gitignore").read_text() == original_gitignore


def test_project_mode_trusts_evaluator_state_at_run_branch_point(ws):  # noqa: ANN001, ANN201
    checker = ws / "accuracy_checker" / "checker.py"
    checker.parent.mkdir()
    checker.write_text("print('v1')\n")
    _init_repo_with_commit(ws)

    checker.write_text("print('operator-authorized v2')\n")
    _git_run(ws, "add", "accuracy_checker/checker.py")
    _git_run(
        ws,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        "update evaluator before VibeSys",
    )
    branch_point = _git_stdout(ws, "rev-parse", "HEAD").strip()

    tracker = _make_project_tracker(ws, run_id="updated-evaluator")
    tracker.init(existing=False)

    assert tracker.trusted_input_baseline == branch_point
    assert tracker.trusted_input_changes() == []

    _git_run(ws, "switch", "main")
    resumed = _make_project_tracker(ws, run_id="updated-evaluator")
    resumed.init(existing=True, trusted_input_baseline=branch_point)

    assert resumed.trusted_input_baseline == branch_point
    assert resumed.trusted_input_changes() == []

    checker.write_text("print('agent tampering')\n")
    assert resumed.trusted_input_changes() == ["accuracy_checker/checker.py"]


def test_project_mode_tracks_bundle_declared_evaluator_path_literally(ws):  # noqa: ANN001, ANN201
    evaluator = ws / "custom[evaluator]"
    evaluator.mkdir()
    checker = evaluator / "check.py"
    checker.write_text("print('ok')\n")
    _init_repo_with_commit(ws)

    tracker = GitTracker(
        ws,
        log=lambda _message: None,
        excluded_dirs=_EXCLUDED,
        mode=GitTrackingMode.USER_PROJECT,
        run_id="custom-evaluator",
        trusted_input_paths=(evaluator.relative_to(ws),),
    )
    tracker.init(existing=False)

    checker.write_text("print('agent tampering')\n")
    assert tracker.trusted_input_changes() == ["custom[evaluator]/check.py"]


@pytest.mark.parametrize("private_name", [".env", ".env.local", "agent.toml"])
def test_project_mode_rejects_private_inputs_in_git_history(ws, private_name):  # noqa: ANN001, ANN201
    (ws / private_name).write_text("SECRET=value\n")
    _init_repo_with_commit(ws)

    tracker = _make_project_tracker(ws, run_id="private-history")
    with pytest.raises(ValueError, match=r"Git history contains private inputs"):
        tracker.init(existing=False)


def test_project_mode_rejects_private_inputs_removed_from_head(ws):  # noqa: ANN001, ANN201
    secret = ws / ".env"
    secret.write_text("SECRET=value\n")
    _init_repo_with_commit(ws)
    secret.unlink()
    _git_run(ws, "add", "-A")
    _git_run(
        ws,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        "remove private input",
    )

    tracker = _make_project_tracker(ws, run_id="removed-private-history")
    with pytest.raises(ValueError, match=r"\.env"):
        tracker.init(existing=False)


def test_project_mode_allows_untracked_private_inputs(ws):  # noqa: ANN001, ANN201
    _init_repo_with_commit(ws)
    (ws / ".env").write_text("SECRET=value\n")

    tracker = _make_project_tracker(ws, run_id="untracked-private")
    tracker.init(existing=False)

    assert _git_stdout(ws, "branch", "--show-current").strip() == "vibesys/untracked-private"


def test_project_mode_rejects_dirty_existing_repository(ws):  # noqa: ANN001, ANN201
    _init_repo_with_commit(ws)
    (ws / "main.py").write_text("VALUE = 2\n")

    tracker = _make_project_tracker(ws, run_id="dirty")
    with pytest.raises(ValueError, match=r"must be clean.*main.py"):
        tracker.init(existing=False)

    assert _git_stdout(ws, "branch", "--show-current").strip() == "main"


def test_project_mode_rejects_parent_repository(tmp_path):  # noqa: ANN001, ANN201
    parent = tmp_path / "parent"
    project = parent / "project"
    project.mkdir(parents=True)
    (parent / "README.md").write_text("parent\n")
    (project / "main.py").write_text("VALUE = 1\n")
    _init_repo_with_commit(parent)

    tracker = _make_project_tracker(project, run_id="nested")
    with pytest.raises(ValueError, match=r"repository root.*containing repository"):
        tracker.init(existing=False)

    assert not (project / ".git").exists()


def test_project_mode_rejects_existing_run_branch(ws):  # noqa: ANN001, ANN201
    tracker = _make_project_tracker(ws, run_id="duplicate")
    tracker.init(existing=False)

    duplicate = _make_project_tracker(ws, run_id="duplicate")
    with pytest.raises(ValueError, match="run branch already exists"):
        duplicate.init(existing=False)


def test_project_snapshot_excludes_framework_private_and_cache_paths(ws):  # noqa: ANN001, ANN201
    tracker = _make_project_tracker(ws)
    tracker.init(existing=False)
    tracker.snapshot_with_framework_metadata(
        "record run",
        {".vs/run.json": '{"run": 1}\n'},
    )

    (ws / "main.py").write_text("VALUE = 2\n")
    (ws / ".vs" / "run.json").write_text('{"forged": true}\n')
    (ws / ".vs" / "untracked.json").write_text("{}\n")
    (ws / ".env").write_text("TOKEN=secret\n")
    (ws / "agent.toml").write_text("provider = 'local'\n")
    cache = ws / "pkg" / "__pycache__" / "module.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"cache")

    tracker.snapshot("candidate edit")

    assert _git_stdout(ws, "show", "HEAD:main.py") == "VALUE = 2\n"
    assert _git_stdout(ws, "show", "HEAD:.vs/run.json") == '{"run": 1}\n'
    committed = _git_stdout(ws, "show", "--format=", "--name-only", "HEAD").splitlines()
    assert committed == ["main.py"]
    assert tracker.pending_changes() == [".vs/run.json", ".vs/untracked.json"]


def test_framework_metadata_snapshot_writes_exact_files_and_candidate(ws):  # noqa: ANN001, ANN201
    tracker = _make_project_tracker(ws)
    tracker.init(existing=False)
    (ws / "main.py").write_text("VALUE = 2\n")

    tracker.snapshot_with_framework_metadata(
        "round 1",
        {
            ".vs/project.toml": "schema = 1\n",
            ".vs/runs/test-run/rounds/0001.json": b'{"round": 1}\n',
        },
    )

    committed = _git_stdout(ws, "show", "--format=", "--name-only", "HEAD").splitlines()
    assert committed == [
        ".vs/project.toml",
        ".vs/runs/test-run/rounds/0001.json",
        "main.py",
    ]
    assert _git_stdout(ws, "status", "--porcelain") == ""


def test_framework_metadata_snapshot_rejects_pending_committed_metadata(ws):  # noqa: ANN001, ANN201
    tracker = _make_project_tracker(ws)
    tracker.init(existing=False)
    tracker.snapshot_with_framework_metadata(
        "record run",
        {".vs/run.json": '{"run": 1}\n'},
    )
    (ws / ".vs" / "run.json").write_text('{"forged": true}\n')

    with pytest.raises(ValueError, match=r"unexpectedly modified.*\.vs/run.json"):
        tracker.snapshot_with_framework_metadata(
            "round 2",
            {".vs/runs/test-run/rounds/0002.json": '{"round": 2}\n'},
        )

    assert not (ws / ".vs" / "runs" / "test-run" / "rounds" / "0002.json").exists()


def test_framework_metadata_snapshot_commits_exact_supplied_update(ws):  # noqa: ANN001, ANN201
    tracker = _make_project_tracker(ws)
    tracker.init(existing=False)
    tracker.snapshot_with_framework_metadata("record run", {".vs/run.json": '{"limit": 1}\n'})
    updated = '{"limit": 2}\n'
    (ws / ".vs" / "run.json").write_text(updated)

    tracker.snapshot_with_framework_metadata("increase limit", {".vs/run.json": updated})

    assert _git_stdout(ws, "show", "HEAD:.vs/run.json") == updated
    assert _git_stdout(ws, "status", "--porcelain") == ""


def test_framework_metadata_snapshot_rejects_supplied_content_mismatch(ws):  # noqa: ANN001, ANN201
    tracker = _make_project_tracker(ws)
    tracker.init(existing=False)
    tracker.snapshot_with_framework_metadata("record run", {".vs/run.json": '{"limit": 1}\n'})
    (ws / ".vs" / "run.json").write_text('{"forged": true}\n')

    with pytest.raises(ValueError, match=r"unexpectedly modified.*\.vs/run.json"):
        tracker.snapshot_with_framework_metadata(
            "increase limit", {".vs/run.json": '{"limit": 2}\n'}
        )


@pytest.mark.parametrize(
    "path",
    ["run.json", ".vs/local/state.json", ".vs/../outside.json"],
)
def test_framework_metadata_snapshot_rejects_nonportable_paths(ws, path):  # noqa: ANN001, ANN201
    tracker = _make_project_tracker(ws)
    tracker.init(existing=False)

    with pytest.raises(ValueError, match=r"below \.vs/.*outside \.vs/local"):
        tracker.snapshot_with_framework_metadata("invalid", {path: "{}\n"})


def test_project_checkout_preserves_all_framework_metadata(ws):  # noqa: ANN001, ANN201
    tracker = _make_project_tracker(ws)
    tracker.init(existing=False)
    (ws / "main.py").write_text("VALUE = 2\n")
    tracker.snapshot_with_framework_metadata(
        "round 1",
        {".vs/runs/test-run/rounds/0001.json": '{"round": 1}\n'},
    )
    round_one = tracker.current_sha()
    assert round_one is not None

    (ws / "main.py").write_text("VALUE = 3\n")
    tracker.snapshot_with_framework_metadata(
        "round 2",
        {".vs/runs/test-run/rounds/0002.json": '{"round": 2}\n'},
    )
    local_state = ws / ".vs" / "local" / "state.json"
    local_state.parent.mkdir(parents=True)
    local_state.write_text("in progress\n")
    untracked_metadata = ws / ".vs" / "diagnostic.txt"
    untracked_metadata.write_text("keep\n")

    assert tracker.checkout_tree(round_one, clean=True)

    assert (ws / "main.py").read_text() == "VALUE = 2\n"
    assert (ws / ".vs" / "runs" / "test-run" / "rounds" / "0002.json").is_file()
    assert local_state.read_text() == "in progress\n"
    assert untracked_metadata.read_text() == "keep\n"
