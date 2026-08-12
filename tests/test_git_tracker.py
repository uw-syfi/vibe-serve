"""Contract tests for canonical project Git history."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from vibesys.run.git_tracker import GitTracker
from vs_project_state import StateFile, StateSnapshot

_IDENTITY = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(root: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(  # noqa: S603  # tracked: #288
        ["git", *args],  # noqa: S607  # tracked: #288
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
        env={**os.environ, **_IDENTITY},
    ).stdout.strip()


def _tracker(root: Path, run_id: str = "test-run") -> GitTracker:
    return GitTracker(
        root,
        run_id=run_id,
        log=lambda _message: None,
        excluded_dirs=("attempts",),
    )


def _initialized_tracker(root: Path, run_id: str = "test-run") -> GitTracker:
    (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    tracker = _tracker(root, run_id)
    tracker.init(existing=False)
    return tracker


def _snapshot(root: str, *files: tuple[str, str | bytes]) -> StateSnapshot:
    """Build the validated project-state value consumed by GitTracker."""
    state_files = tuple(
        sorted(
            (
                StateFile(
                    relative_path=PurePosixPath(path),
                    contents=contents.encode() if isinstance(contents, str) else contents,
                )
                for path, contents in files
            ),
            key=lambda item: item.relative_path.as_posix(),
        )
    )
    return StateSnapshot(namespace_root=PurePosixPath(root), files=state_files)


def _initialize_existing_repository(root: Path) -> str:
    _git(root, "init", "-q", "-b", "main")
    (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", "main.py")
    _git(root, "commit", "-q", "-m", "baseline")
    return _git(root, "rev-parse", "HEAD")


def test_fresh_project_owns_repository_branch_and_local_excludes(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "agent.toml").write_text("secret = true\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("TOKEN=secret\n", encoding="utf-8")

    tracker = _tracker(tmp_path, "round-trip")
    tracker.init(existing=False)

    assert tracker.root == tmp_path.resolve()
    assert tracker.history_root == tmp_path.resolve()
    assert tracker.project_branch == "vibesys/round-trip"
    assert _git(tmp_path, "branch", "--show-current") == tracker.project_branch
    assert tracker.trusted_input_baseline == tracker.current_sha()
    assert _git(tmp_path, "ls-files").splitlines() == ["main.py"]
    excludes = (tmp_path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.vs/local/" in excludes
    assert "/agent.toml" in excludes
    assert "/.env.*" in excludes
    assert "attempts/" in excludes
    assert not (tmp_path / ".gitignore").exists()


def test_existing_repository_starts_from_clean_baseline(tmp_path: Path) -> None:
    baseline = _initialize_existing_repository(tmp_path)

    tracker = _tracker(tmp_path)
    tracker.init(existing=False)

    assert tracker.trusted_input_baseline == baseline
    assert tracker.current_sha() == baseline
    assert _git(tmp_path, "branch", "--show-current") == "vibesys/test-run"


def test_existing_repository_rejects_dirty_or_unborn_history(tmp_path: Path) -> None:
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    _initialize_existing_repository(dirty)
    (dirty / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be clean"):
        _tracker(dirty).init(existing=False)

    unborn = tmp_path / "unborn"
    unborn.mkdir()
    _git(unborn, "init", "-q", "-b", "main")
    with pytest.raises(ValueError, match="no baseline commit"):
        _tracker(unborn).init(existing=False)


def test_project_must_be_repository_root(tmp_path: Path) -> None:
    _initialize_existing_repository(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()

    with pytest.raises(ValueError, match="repository root"):
        _tracker(nested).init(existing=False)


def test_resume_selects_existing_run_branch_and_baseline(tmp_path: Path) -> None:
    tracker = _initialized_tracker(tmp_path)
    baseline = tracker.trusted_input_baseline
    assert baseline is not None
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    tracker.snapshot("candidate")
    expected = tracker.current_sha()
    _git(tmp_path, "switch", "main")

    resumed = _tracker(tmp_path)
    resumed.init(existing=True, trusted_input_baseline=baseline)

    assert _git(tmp_path, "branch", "--show-current") == "vibesys/test-run"
    assert resumed.current_sha() == expected
    assert resumed.trusted_input_baseline == baseline


def test_resume_requires_repository_and_existing_run_branch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no Git repository"):
        _tracker(tmp_path).init(existing=True)

    _initialize_existing_repository(tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        _tracker(tmp_path).init(existing=True)


def test_private_inputs_must_not_be_recoverable_from_history(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "agent.toml").write_text("secret = true\n", encoding="utf-8")
    _git(tmp_path, "add", "agent.toml")
    _git(tmp_path, "commit", "-q", "-m", "leak")
    (tmp_path / "agent.toml").unlink()
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "delete leak")

    with pytest.raises(ValueError, match="private inputs"):
        _tracker(tmp_path).init(existing=False)


def test_snapshot_commits_candidate_paths_but_never_local_or_private_state(
    tmp_path: Path,
) -> None:
    tracker = _initialized_tracker(tmp_path)
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / ".vs" / "local").mkdir(parents=True)
    (tmp_path / ".vs" / "local" / "state.json").write_text("{}\n", encoding="utf-8")
    portable = tmp_path / ".vs" / "runs" / "test-run" / "rogue.json"
    portable.parent.mkdir(parents=True)
    portable.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", "--force", ".vs/runs/test-run/rogue.json")
    (tmp_path / "agent.toml").write_text("secret = true\n", encoding="utf-8")
    (tmp_path / "module.pyc").write_bytes(b"cache")

    tracker.snapshot("candidate")

    assert _git(tmp_path, "show", "HEAD:main.py") == "VALUE = 2"
    tracked = set(_git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    assert tracked == {"main.py"}


def test_framework_snapshot_commits_only_supplied_exact_metadata(tmp_path: Path) -> None:
    tracker = _initialized_tracker(tmp_path)
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")

    tracker.snapshot_with_framework_metadata(
        "round 1",
        _snapshot(
            ".vs/runs/test-run/agent",
            ("rounds/0001.json", b'{"round":1}\n'),
        ),
    )

    assert _git(tmp_path, "show", "HEAD:main.py") == "VALUE = 2"
    assert _git(tmp_path, "show", "HEAD:.vs/runs/test-run/agent/rounds/0001.json") == (
        '{"round":1}'
    )
    assert not _git(tmp_path, "ls-files", ".vs/local")


def test_framework_snapshot_rejects_symlink_traversal(tmp_path: Path) -> None:
    tracker = _initialized_tracker(tmp_path)
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / ".vs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        tracker.snapshot_with_framework_metadata(
            "invalid",
            _snapshot(".vs", ("project.json", "{}\n")),
        )


def test_framework_snapshot_refuses_unexpected_metadata_edits(tmp_path: Path) -> None:
    tracker = _initialized_tracker(tmp_path)
    tracker.snapshot_with_framework_metadata(
        "initialize",
        _snapshot(".vs", ("project.json", "old\n")),
    )
    project_metadata = tmp_path / ".vs" / "project.json"
    project_metadata.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpectedly modified"):
        tracker.snapshot_with_framework_metadata(
            "round",
            _snapshot(
                ".vs/runs/test-run/agent",
                ("rounds/0001.json", "round\n"),
            ),
        )
    with pytest.raises(ValueError, match="unexpectedly modified"):
        tracker.snapshot_with_framework_metadata(
            "round",
            _snapshot(".vs", ("project.json", "expected\n")),
        )


def test_framework_state_snapshot_replaces_one_namespace_exactly(tmp_path: Path) -> None:
    tracker = _initialized_tracker(tmp_path)
    tracker.snapshot_with_framework_metadata(
        "initialize metadata",
        _snapshot(
            ".vs",
            ("project.json", "project\n"),
            ("runs/test-run/evolve/keep.json", "old\n"),
            ("runs/test-run/evolve/remove.json", "remove\n"),
        ),
    )

    tracker.snapshot_framework_state(
        "rotate population",
        _snapshot(
            ".vs/runs/test-run/evolve",
            ("keep.json", "new\n"),
            ("nested/add.json", b"add\n"),
        ),
    )

    tracked = set(
        _git(
            tmp_path,
            "ls-tree",
            "-r",
            "--name-only",
            "HEAD",
            ".vs/runs/test-run/evolve",
        ).splitlines()
    )
    assert tracked == {
        ".vs/runs/test-run/evolve/keep.json",
        ".vs/runs/test-run/evolve/nested/add.json",
    }
    assert _git(tmp_path, "show", "HEAD:.vs/runs/test-run/evolve/keep.json") == "new"
    assert _git(tmp_path, "show", "HEAD:.vs/project.json") == "project"


def test_framework_state_snapshot_leaves_candidate_changes_pending(tmp_path: Path) -> None:
    tracker = _initialized_tracker(tmp_path)
    tracker.snapshot_with_framework_metadata(
        "initialize metadata",
        _snapshot(
            ".vs/runs/test-run/evolve",
            ("state.json", "old\n"),
        ),
    )
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "main.py")

    tracker.snapshot_framework_state(
        "update state",
        _snapshot(
            ".vs/runs/test-run/evolve",
            ("state.json", "new\n"),
        ),
    )

    assert _git(tmp_path, "show", "HEAD:main.py") == "VALUE = 1"
    assert _git(tmp_path, "show", "HEAD:.vs/runs/test-run/evolve/state.json") == "new"
    assert _git(tmp_path, "status", "--short", "main.py") == "M  main.py"


def test_framework_state_snapshot_protects_metadata_outside_namespace(
    tmp_path: Path,
) -> None:
    tracker = _initialized_tracker(tmp_path)
    tracker.snapshot_with_framework_metadata(
        "initialize metadata",
        _snapshot(
            ".vs",
            ("project.json", "project\n"),
            ("runs/test-run/evolve/state.json", "old\n"),
        ),
    )
    (tmp_path / ".vs" / "project.json").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="other committed VibeSys metadata"):
        tracker.snapshot_framework_state(
            "replace",
            _snapshot(
                ".vs/runs/test-run/evolve",
                ("state.json", "new\n"),
            ),
        )

    assert (tmp_path / ".vs" / "runs" / "test-run" / "evolve" / "state.json").read_text(
        encoding="utf-8"
    ) == "old\n"


@pytest.mark.parametrize(
    "namespace",
    [
        ".vs",
        ".vs/runs/test-run",
        ".vs/runs/other/evolve",
    ],
)
def test_framework_state_snapshot_requires_dedicated_current_run_namespace(
    tmp_path: Path,
    namespace: str,
) -> None:
    tracker = _initialized_tracker(tmp_path)

    with pytest.raises(ValueError, match="dedicated directory"):
        tracker.snapshot_framework_state(
            "invalid",
            _snapshot(namespace),
        )


def test_checkout_restores_candidate_tree_and_preserves_all_vs_state(tmp_path: Path) -> None:
    tracker = _initialized_tracker(tmp_path)
    first = tracker.current_sha()
    assert first is not None
    (tmp_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    tracker.snapshot_with_framework_metadata(
        "candidate",
        _snapshot(".vs", ("project.json", "portable\n")),
    )
    (tmp_path / ".vs" / "project.json").write_text("new portable\n", encoding="utf-8")
    local = tmp_path / ".vs" / "local" / "active.json"
    local.parent.mkdir(parents=True)
    local.write_text("local\n", encoding="utf-8")
    (tmp_path / "scratch.txt").write_text("delete me\n", encoding="utf-8")

    assert tracker.checkout_tree(first, clean=True)

    assert (tmp_path / "main.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (tmp_path / ".vs" / "project.json").read_text(encoding="utf-8") == "new portable\n"
    assert local.read_text(encoding="utf-8") == "local\n"
    assert not (tmp_path / "scratch.txt").exists()


def test_trusted_input_changes_compare_against_branch_point(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    objective = tmp_path / "OBJECTIVE.md"
    objective.write_text("original\n", encoding="utf-8")
    tracker = _tracker(tmp_path)
    tracker.init(existing=False)

    objective.write_text("changed\n", encoding="utf-8")
    assert tracker.trusted_input_changes() == ["OBJECTIVE.md"]
    tracker.snapshot("changed trusted input")
    assert tracker.trusted_input_changes() == ["OBJECTIVE.md"]


def test_candidate_worktree_is_local_and_retained_by_durable_ref(tmp_path: Path) -> None:
    tracker = _initialized_tracker(tmp_path, "durable")
    baseline = tracker.current_sha()
    assert baseline is not None
    worktree = tmp_path / ".vs" / "local" / "candidates" / "candidate-1"
    tracker.add_worktree(worktree, baseline)
    (worktree / "main.py").write_text("VALUE = 42\n", encoding="utf-8")
    _git(worktree, "add", "main.py")
    _git(worktree, "commit", "-q", "-m", "candidate")
    candidate_sha = _git(worktree, "rev-parse", "HEAD")

    ref = tracker.retain_worktree(worktree, "candidate-1")
    tracker.remove_worktree(worktree)
    _git(tmp_path, "reflog", "expire", "--expire=now", "--all")
    _git(tmp_path, "gc", "--prune=now")

    assert ref == "refs/vibesys/durable/candidates/candidate-1"
    assert _git(tmp_path, "rev-parse", ref) == candidate_sha
    assert _git(tmp_path, "show", f"{ref}:main.py") == "VALUE = 42"
    assert not worktree.exists()


@pytest.mark.parametrize(
    "path",
    ["candidate", ".vs/local", ".vs/peer", "../outside"],
)
def test_candidate_worktrees_must_be_below_vs_local(tmp_path: Path, path: str) -> None:
    tracker = _initialized_tracker(tmp_path)
    candidate = tmp_path / path

    with pytest.raises(ValueError, match="below"):
        tracker.add_worktree(candidate, "HEAD")
    with pytest.raises(ValueError, match="below"):
        tracker.remove_worktree(candidate)


def test_candidate_ref_rejects_unsafe_id_or_unknown_commit(tmp_path: Path) -> None:
    tracker = _initialized_tracker(tmp_path)

    with pytest.raises(ValueError, match="candidate id"):
        tracker.retain_candidate("../escape", "HEAD")
    with pytest.raises(ValueError, match="not a commit"):
        tracker.retain_candidate("candidate", "missing")
