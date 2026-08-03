"""Git snapshot tracking over an experiment workspace.

``GitTracker`` owns the repo-over-workspace history kept when
``--git-tracking`` is enabled: per-round snapshot commits, checkout of prior
round trees, and detection of tampering with evaluator-owned inputs.  It is
deliberately independent of the rest of the run context — construction takes
the workspace root, a log callback, and the directory names to keep out of
history; nothing here touches sandboxes or backends.
"""

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path


class GitTracker:
    """Snapshot tracking for one workspace directory.

    Construction is side-effect free; ``init`` creates (or, on resume,
    validates) the repository.  ``run`` is the public escape hatch for
    loop-specific git plumbing that has no dedicated method yet.
    """

    _GIT_ENV_STATIC = {
        "GIT_AUTHOR_NAME": "vibesys",
        "GIT_AUTHOR_EMAIL": "vibesys@local",
        "GIT_COMMITTER_NAME": "vibesys",
        "GIT_COMMITTER_EMAIL": "vibesys@local",
    }

    # Compiled-accelerator artifacts an agent may emit into the workspace.
    # Large and never wanted in a per-round checkpoint. The Neuron compile cache
    # is bind-mounted *outside* the workspace, but a stray trace/compile call
    # pointed at the workspace (or a torch.compile dump) would otherwise be
    # committed and bloat history across rounds.
    _ARTIFACT_GITIGNORE_PATTERNS: tuple[str, ...] = (
        "*.neff",
        "*.ntff",
        "*.neuron",
        "neuroncc_compile_workdir/",
        "neuron-compile-cache/",
    )

    _TRUSTED_INPUT_PATHS: tuple[str, ...] = (
        "OBJECTIVE.md",
        "vibesys.input.toml",
        "reference",
        "accuracy_checker",
        "benchmark",
        "_input_libs",
        "_evaluator",
    )

    def __init__(
        self,
        root: Path,
        *,
        log: Callable[[str], None],
        excluded_dirs: Iterable[str] = (),
    ) -> None:
        self.root = root
        self._log = log
        self._excluded_dirs = frozenset(excluded_dirs)
        self._trusted_input_baseline: str | None = None
        self._git_dir: Path | None = None
        self._work_tree: Path | None = None
        # Keep dynamic snapshot exclusions outside the candidate worktree. An
        # isolated agent may create or replace ``root/.git`` as another user;
        # framework bookkeeping must remain host-owned and writable.
        self._exclude_file = self.root.parent / ".vibesys-git-excludes" / self.root.name

    @property
    def _GIT_ENV(self) -> dict[str, str]:
        """Git env pinned to the repository selected during initialization."""
        safe_directory = self._work_tree or self.root
        config = (
            ("safe.directory", str(safe_directory)),
            ("core.excludesFile", str(self._exclude_file)),
        )
        result = {
            **self._GIT_ENV_STATIC,
            "GIT_CONFIG_COUNT": str(len(config)),
        }
        for index, (key, value) in enumerate(config):
            result[f"GIT_CONFIG_KEY_{index}"] = key
            result[f"GIT_CONFIG_VALUE_{index}"] = value
        if self._git_dir is not None and self._work_tree is not None:
            result["GIT_DIR"] = str(self._git_dir)
            result["GIT_WORK_TREE"] = str(self._work_tree)
        return result

    def run(
        self, cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        """Run a git command in the workspace, logging stderr on failure."""
        if env is None:
            env = {**os.environ, **self._GIT_ENV}
        result = subprocess.run(cmd, cwd=self.root, capture_output=True, env=env)
        if check and result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            self._log(f"[git-tracking] command failed: {' '.join(cmd)}")
            self._log(f"[git-tracking] exit code {result.returncode}: {stderr}")
            result.check_returncode()
        return result

    def init(self, existing: bool, *, trusted_input_baseline: str | None = None) -> None:
        """Initialize or validate Git tracking for the unified workspace.

        Experiment directories are themselves Git repositories. When the
        workspace already lives below one, use that repository and keep every
        operation scoped to the workspace. Standalone callers still get a
        repository rooted directly at ``self.root``.
        """
        if existing:
            if not self._inside_work_tree():
                raise ValueError(
                    f"--git-tracking with --resume but no git repository in {self.root}"
                )
            self._bind_repository()
            if trusted_input_baseline is not None:
                self._trusted_input_baseline = self._resolve_trusted_input_baseline(
                    trusted_input_baseline
                )
                self._log(
                    f"[git-tracking] trusted input baseline: {self._trusted_input_baseline[:12]}"
                )
            return

        if trusted_input_baseline is not None:
            raise ValueError("trusted input baseline is only valid when resuming a run")

        if not self._inside_work_tree():
            self.run(["git", "init"])
        self._bind_repository()

        gitignore = self.root / ".gitignore"
        existing_gitignore = gitignore.read_text() if gitignore.is_file() else ""
        if existing_gitignore and not existing_gitignore.endswith("\n"):
            existing_gitignore += "\n"
        gitignore.write_text(existing_gitignore + self._workspace_gitignore())

        self._add_all()
        self.run(["git", "commit", "-m", "initial: workspace setup"])

    def add_worktree(self, worktree_dir: Path, commit: str) -> None:
        """Create a detached linked worktree at *commit*.

        The worktree gets its own working tree, index, and (detached) HEAD but
        shares this repository's object store, so a commit made in the worktree
        is immediately reachable by sha from the main repo — exactly what a
        per-candidate evolve worktree needs (isolated edits, one shared
        lineage). ``git worktree add`` mutates the main repo's
        ``.git/worktrees`` admin area, so callers must serialize concurrent
        adds; committing *inside* a worktree afterwards is independent per
        worktree and safe to run concurrently.
        """
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        self.run(["git", "worktree", "add", "--detach", str(worktree_dir), commit])

    def remove_worktree(self, worktree_dir: Path) -> None:
        """Unregister a linked worktree and delete its directory (best-effort).

        ``git worktree remove`` unregisters the worktree, but it can leave the
        directory on disk — e.g. when the editor container wrote scratch files
        (``__pycache__``, ``.pytest_cache``) into the bind-mounted tree that
        ``git`` then declines to delete. Follow up with an explicit recursive
        delete so per-candidate workspaces don't accumulate across a run, then
        prune any stale admin entry. Both the ``git`` failure and any
        undeletable leftovers are non-fatal: unregistration is what matters for
        correctness, so ``ignore_errors`` keeps a stubborn file from sinking the
        run.
        """
        self.run(["git", "worktree", "remove", "--force", str(worktree_dir)], check=False)
        if Path(worktree_dir).exists():
            shutil.rmtree(worktree_dir, ignore_errors=True)
        self.run(["git", "worktree", "prune"], check=False)

    def snapshot(self, label: str) -> None:
        """Commit current workspace state with *label* as the commit message."""
        self._add_all()
        # git diff --cached --quiet exits 1 when there are staged changes
        has_changes = (
            self.run(
                ["git", "diff", "--cached", "--quiet"],
                check=False,
            ).returncode
            != 0
        )
        if has_changes:
            self.run(["git", "commit", "-m", label])
        else:
            self._log(f"[git-tracking] no changes to commit for '{label}'")

    def current_sha(self) -> str | None:
        """Return the HEAD commit sha, or ``None`` if it cannot be resolved."""
        try:
            result = self.run(["git", "rev-parse", "HEAD"], check=False)
            if result.returncode != 0:
                return None
            return result.stdout.decode(errors="replace").strip()
        except Exception:
            return None

    @property
    def history_root(self) -> Path | None:
        """Return the repository worktree containing checkpoint history.

        Sandboxed agents edit only ``root``, which can be a subdirectory of
        the experiment repository. Run environments mount this larger root
        read-only so agents can inspect the commits the framework advertises
        without granting them ownership of snapshot bookkeeping.
        """
        return self._work_tree

    def pending_changes(self) -> list[str]:
        """Return tracked and untracked workspace paths changed since ``HEAD``.

        Role-isolated agents such as the orchestrator and judge are allowed to
        inspect the candidate but not mutate it.  Callers checkpoint framework
        state first, then use this method to detect any writes the agent made
        during its turn before restoring the checkpoint.
        """
        result = self.run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                ".",
            ]
        )
        prefix_result = self.run(["git", "rev-parse", "--show-prefix"])
        prefix = prefix_result.stdout.decode(errors="replace").strip()
        return sorted(
            line[3:].removeprefix(prefix) if prefix else line[3:]
            for line in result.stdout.decode(errors="replace").splitlines()
            if len(line) > 3
        )

    def checkout_tree(
        self,
        sha: str,
        *,
        clean: bool = False,
        preserve_paths: Iterable[str | Path] = (),
    ) -> bool:
        """Materialize *sha*'s tree into the working directory.

        Restores both the index and worktree from *sha* so paths introduced
        after that snapshot are deleted as well as modified paths being reset.
        HEAD stays where it is, so the next commit produces a new child commit
        rather than rewriting history. With ``clean=True``, untracked files
        left over from a prior failed attempt are removed via ``git clean
        -fd``. Files below workspace-relative ``preserve_paths`` are captured
        before the restore and reapplied afterwards. This is intended for
        framework-owned memory that must survive a candidate-code rollback.
        """
        preserved: dict[Path, bytes] = {}
        try:
            preserved = self._capture_preserved_paths(preserve_paths)
            self.run(
                [
                    "git",
                    "restore",
                    f"--source={sha}",
                    "--staged",
                    "--worktree",
                    "--",
                    ".",
                ]
            )
            if clean:
                self.run(["git", "clean", "-fd", "--", "."], check=False)
            self._restore_preserved_paths(preserved)
            return True
        except Exception as exc:
            try:
                self._restore_preserved_paths(preserved)
            except Exception as preserve_exc:
                self._log(
                    "[warn] failed to restore preserved workspace memory after "
                    f"tree restore error: {preserve_exc}"
                )
            self._log(f"[warn] git tree restore {sha[:8]} failed: {exc}")
            return False

    def _capture_preserved_paths(self, paths: Iterable[str | Path]) -> dict[Path, bytes]:
        """Read regular files below workspace-relative *paths*."""
        preserved: dict[Path, bytes] = {}
        for raw_path in paths:
            relative = Path(raw_path)
            if relative.is_absolute() or relative == Path(".") or ".." in relative.parts:
                raise ValueError(f"preserved path must be workspace-relative: {raw_path}")
            source = self.root / relative
            if source.is_file():
                preserved[relative] = source.read_bytes()
            elif source.is_dir():
                for child in source.rglob("*"):
                    if child.is_file():
                        preserved[child.relative_to(self.root)] = child.read_bytes()
        return preserved

    def _restore_preserved_paths(self, preserved: dict[Path, bytes]) -> None:
        """Reapply files captured by :meth:`_capture_preserved_paths`."""
        for relative, content in preserved.items():
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

    def trusted_input_changes(self) -> list[str]:
        """Return evaluator-owned paths changed since the trusted baseline."""
        initial_commit = self._trusted_input_baseline
        if initial_commit is None:
            baseline = self.run(
                [
                    "git",
                    "log",
                    "--diff-filter=A",
                    "--format=%H",
                    "--reverse",
                    "--",
                    *self._TRUSTED_INPUT_PATHS,
                ]
            )
            commits = baseline.stdout.decode().splitlines()[0:1]
            if not commits:
                return ["unable to resolve the initial workspace commit"]
            initial_commit = commits[0]

        pathspec = ["--", *self._TRUSTED_INPUT_PATHS]
        committed = self.run(["git", "diff", "--name-only", f"{initial_commit}..HEAD", *pathspec])
        pending = self.run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                *pathspec,
            ]
        )

        prefix_result = self.run(["git", "rev-parse", "--show-prefix"])
        prefix = prefix_result.stdout.decode(errors="replace").strip()

        def workspace_relative(path: str) -> str:
            return path.removeprefix(prefix) if prefix else path

        changes = {
            workspace_relative(line)
            for line in committed.stdout.decode(errors="replace").splitlines()
            if line
        }
        changes.update(
            workspace_relative(line[3:])
            for line in pending.stdout.decode(errors="replace").splitlines()
            if len(line) > 3
        )
        return sorted(changes)

    def _resolve_trusted_input_baseline(self, revision: str) -> str:
        """Resolve an operator-authorized trusted-input baseline revision.

        The revision must already be an ancestor of the resumed workspace's
        current HEAD. Pending trusted-input edits are still reported, and any
        later committed edits remain visible in the baseline-to-HEAD diff.
        """
        resolved = self.run(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
            check=False,
        )
        if resolved.returncode != 0:
            raise ValueError(f"trusted input baseline {revision!r} is not a commit")
        commit = resolved.stdout.decode(errors="replace").strip()
        ancestor = self.run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
            check=False,
        )
        if ancestor.returncode != 0:
            raise ValueError(f"trusted input baseline {revision!r} is not an ancestor of HEAD")
        return commit

    def _workspace_gitignore(self) -> str:
        """Contents of the workspace ``.gitignore`` (excluded dirs + artifacts)."""
        lines = sorted(self._excluded_dirs) + list(self._ARTIFACT_GITIGNORE_PATTERNS)
        return "\n".join(lines) + "\n"

    # -- snapshot resilience --------------------------------------------------
    #
    # On the Docker/Modal paths the sandbox runs as root and writes files into
    # the bind-mounted workspace.  Most land mode-644 (host-readable), but a
    # tool may emit a restrictive file the *host* user running `git add` cannot
    # read (e.g. neuron-explorer's mode-600 ``system_profile.json``).  A single
    # such file makes ``git add -A`` exit 128 and would otherwise abort the whole
    # run.  These are always transient scratch artifacts we never want in a
    # checkpoint, so we exclude them through a framework-owned exclude file
    # outside the worktree rather than fail.

    def _collect_unreadable(self) -> list[str]:
        """Workspace-relative paths the snapshotting user cannot read.

        Walks the worktree (skipping ``.git``, never following symlinks) and
        records files lacking ``R_OK`` and directories lacking ``R_OK|X_OK``
        (an unsearchable dir hides its whole subtree from ``git add`` too).
        """
        unreadable: list[str] = []
        root = str(self.root)
        for dirpath, dirnames, filenames in os.walk(root):
            if ".git" in dirnames:
                dirnames.remove(".git")
            kept = []
            for d in dirnames:
                full = os.path.join(dirpath, d)
                if os.access(full, os.R_OK | os.X_OK):
                    kept.append(d)
                else:
                    unreadable.append(os.path.relpath(full, root))
            dirnames[:] = kept  # prune unsearchable dirs from the walk
            for f in filenames:
                full = os.path.join(dirpath, f)
                if not os.access(full, os.R_OK):
                    unreadable.append(os.path.relpath(full, root))
        return unreadable

    @staticmethod
    def _unreadable_from_stderr(stderr: str) -> list[str]:
        """Parse paths git reported it could not index from *stderr*.

        Git prints e.g. ``error: open("foo"): Permission denied`` and
        ``error: unable to index file 'foo'``.
        """
        paths: list[str] = []
        for m in re.finditer(r'(?:open\("|unable to index file \')([^"\']+)', stderr):
            paths.append(m.group(1))
        return paths

    def _exclude_paths(self, rel_paths: list[str]) -> None:
        """Append *rel_paths* to the framework-owned Git exclude file."""
        rel_paths = [p for p in dict.fromkeys(rel_paths) if p]
        if not rel_paths:
            return
        exclude_file = self._exclude_file
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_file.read_text() if exclude_file.exists() else ""
        have = set(existing.splitlines())
        new = [self._exclude_pattern(p) for p in rel_paths]
        new = [p for p in new if p not in have]
        if not new:
            return
        prefix = "" if (not existing or existing.endswith("\n")) else "\n"
        exclude_file.write_text(existing + prefix + "\n".join(new) + "\n")
        shown = ", ".join(new[:5]) + ("…" if len(new) > 5 else "")
        self._log(f"[git-tracking] excluded {len(new)} unreadable path(s) from snapshot: {shown}")

    def _add_all(self) -> None:
        """``git add -A``, resilient to files the host user cannot read.

        Excludes unreadable paths up front, then retries on any residual
        permission failure (a file may appear between the scan and the add).
        """
        self._exclude_paths(self._collect_unreadable())
        for _ in range(3):
            result = self.run(["git", "add", "-A", "--", "."], check=False)
            if result.returncode == 0:
                return
            stderr = result.stderr.decode(errors="replace")
            offenders = self._unreadable_from_stderr(stderr)
            if not offenders:
                break  # failure unrelated to unreadable files — surface it
            self._exclude_paths(offenders)
        # Final attempt: let run() raise with full diagnostics if it still fails.
        self.run(["git", "add", "-A", "--", "."])

    def _bind_repository(self) -> None:
        """Pin future commands to the repository currently containing ``root``.

        Agents can run tools such as plain ``uv init`` that create a nested
        ``.git`` directory after the framework initialized tracking. Without
        explicit ``GIT_DIR``/``GIT_WORK_TREE``, later commands silently switch
        repositories based on the current directory.
        """
        git_dir = self.run(["git", "rev-parse", "--absolute-git-dir"])
        work_tree = self.run(["git", "rev-parse", "--show-toplevel"])
        self._git_dir = Path(git_dir.stdout.decode(errors="replace").strip()).resolve()
        self._work_tree = Path(work_tree.stdout.decode(errors="replace").strip()).resolve()

    def _exclude_pattern(self, rel_path: str) -> str:
        """Return an exact repository-root-relative ignore pattern."""
        target = Path(rel_path)
        if self._work_tree is not None:
            try:
                workspace_prefix = self.root.resolve().relative_to(self._work_tree)
                # The proactive host scan reports workspace-relative paths,
                # while Git's stderr can report worktree-relative paths. Do
                # not apply the workspace prefix twice on the retry path.
                prefix_parts = workspace_prefix.parts
                if target.parts[: len(prefix_parts)] != prefix_parts:
                    target = workspace_prefix / target
            except ValueError:
                pass
        return "/" + target.as_posix().lstrip("/")

    def _inside_work_tree(self) -> bool:
        result = self.run(["git", "rev-parse", "--is-inside-work-tree"], check=False)
        return result.returncode == 0 and result.stdout.decode().strip() == "true"
