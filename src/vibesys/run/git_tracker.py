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
from collections.abc import Callable, Iterable, Mapping
from enum import StrEnum
from pathlib import Path

from vibesys.run.project_policy import TRUSTED_PROJECT_INPUT_PATHS


def _normalize_project_paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    """Normalize safe repository-relative paths used in literal Git pathspecs."""
    normalized: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if (
            path.is_absolute()
            or path == Path()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(f"project path must be a normalized relative path: {raw}")  # noqa: TRY003  # tracked: #288
        normalized.append(path)
    return tuple(normalized)


class GitTrackingMode(StrEnum):
    """Repository ownership model used by :class:`GitTracker`."""

    LEGACY_WORKSPACE = "legacy-workspace"
    USER_PROJECT = "user-project"


class GitTracker:
    """Snapshot tracking for one workspace directory.

    Construction is side-effect free; ``init`` creates (or, on resume,
    validates) the repository.  ``run`` is the public escape hatch for
    loop-specific git plumbing that has no dedicated method yet.
    """

    _GIT_ENV_STATIC = {  # noqa: RUF012  # tracked: #288
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
        "*.otlp.ndjson",
        "neuroncc_compile_workdir/",
        "neuron-compile-cache/",
    )

    _TRUSTED_INPUT_PATHS = TRUSTED_PROJECT_INPUT_PATHS

    _PROJECT_LOCAL_EXCLUDE_PATTERNS: tuple[str, ...] = (
        "/.vs/local/",
        "/.env",
        "/.env.*",
        "/agent.toml",
        "*.py[co]",
        "__pycache__/",
        ".mypy_cache/",
        ".pytest_cache/",
        ".ruff_cache/",
    )

    _PRIVATE_PROJECT_INPUTS: tuple[str, ...] = (
        ".env",
        ".env.*",
        "agent.toml",
    )

    def __init__(  # noqa: D107, PLR0913  # tracked: #288
        self,
        root: Path,
        *,
        log: Callable[[str], None],
        excluded_dirs: Iterable[str] = (),
        mode: GitTrackingMode = GitTrackingMode.LEGACY_WORKSPACE,
        run_id: str | None = None,
        trusted_input_paths: Iterable[str | Path] = (),
    ) -> None:
        self.root = root
        self._log = log
        self._excluded_dirs = frozenset(excluded_dirs)
        self.mode = GitTrackingMode(mode)
        self.run_id = run_id
        self._trusted_input_paths = tuple(
            dict.fromkeys(
                (
                    *_normalize_project_paths(self._TRUSTED_INPUT_PATHS),
                    *_normalize_project_paths(trusted_input_paths),
                )
            )
        )
        self._trusted_input_baseline: str | None = None
        self._git_dir: Path | None = None
        self._work_tree: Path | None = None
        # Keep dynamic snapshot exclusions outside the candidate worktree. An
        # isolated agent may create or replace ``root/.git`` as another user;
        # framework bookkeeping must remain host-owned and writable.
        self._exclude_file = self.root.parent / ".vibesys-git-excludes" / self.root.name

    @property
    def _GIT_ENV(self) -> dict[str, str]:  # noqa: N802  # tracked: #288
        """Git env pinned to the repository selected during initialization."""
        safe_directory = self._work_tree or self.root
        config = [("safe.directory", str(safe_directory))]
        if self.mode is GitTrackingMode.LEGACY_WORKSPACE:
            config.append(("core.excludesFile", str(self._exclude_file)))
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
        result = subprocess.run(cmd, cwd=self.root, capture_output=True, env=env)  # noqa: PLW1510, S603  # tracked: #288
        if check and result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            self._log(f"[git-tracking] command failed: {' '.join(cmd)}")
            self._log(f"[git-tracking] exit code {result.returncode}: {stderr}")
            result.check_returncode()
        return result

    def init(self, existing: bool, *, trusted_input_baseline: str | None = None) -> None:  # noqa: FBT001  # tracked: #288
        """Initialize or validate Git tracking for the unified workspace.

        Experiment directories are themselves Git repositories. When the
        workspace already lives below one, use that repository and keep every
        operation scoped to the workspace. Standalone callers still get a
        repository rooted directly at ``self.root``.
        """
        if self.mode is GitTrackingMode.USER_PROJECT:
            self._init_user_project(
                existing=existing,
                trusted_input_baseline=trusted_input_baseline,
            )
            return

        if existing:
            if not self._inside_work_tree():
                raise ValueError(  # noqa: TRY003  # tracked: #288
                    f"--git-tracking with --resume but no git repository in {self.root}"
                )
            self._bind_repository()
            if trusted_input_baseline is not None:
                self.configure_trusted_input_baseline(trusted_input_baseline)
            return

        if trusted_input_baseline is not None:
            raise ValueError("trusted input baseline is only valid when resuming a run")  # noqa: TRY003  # tracked: #288

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
        self._commit_staged(label)

    def _commit_staged(self, label: str) -> None:
        """Commit the current index, logging when it contains no changes."""
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

    def snapshot_with_framework_metadata(
        self,
        label: str,
        metadata: Mapping[str | Path, str | bytes],
    ) -> None:
        """Commit candidate changes with exact framework-authored ``.vs`` files.

        This is the only project-mode snapshot API that stages ``.vs``. The
        framework supplies complete file contents so the tracker can verify
        existing committed metadata before it writes anything. Local runtime
        state under ``.vs/local`` is never accepted or staged.
        """
        if self.mode is not GitTrackingMode.USER_PROJECT:
            raise ValueError("framework metadata snapshots require user-project mode")  # noqa: TRY003  # tracked: #288

        destinations = self._framework_metadata_destinations(metadata)
        pending = self._pending_committed_framework_metadata()
        supplied = {relative.as_posix(): content for relative, _, content in destinations}
        unexpected = [path for path in pending if path not in supplied]
        mismatched = [
            relative.as_posix()
            for relative, destination, content in destinations
            if relative.as_posix() in pending
            and destination.read_bytes()
            != (content.encode("utf-8") if isinstance(content, str) else content)
        ]
        if unexpected or mismatched:
            shown = ", ".join(sorted({*unexpected, *mismatched}))
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"refusing to overwrite unexpectedly modified committed VibeSys metadata: {shown}"
            )

        self._add_all()
        for relative, destination, content in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                destination.write_text(content)
            else:
                destination.write_bytes(content)
            self.run(["git", "add", "--force", "--", relative.as_posix()])
        self._commit_staged(label)

    def current_sha(self) -> str | None:
        """Return the HEAD commit sha, or ``None`` if it cannot be resolved."""
        try:
            result = self.run(["git", "rev-parse", "HEAD"], check=False)
            if result.returncode != 0:
                return None
            return result.stdout.decode(errors="replace").strip()
        except Exception:  # noqa: BLE001  # tracked: #288
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

    @property
    def trusted_input_baseline(self) -> str | None:
        """Return the resolved commit used as the trusted-input baseline.

        Fresh user-project runs capture their branch-point commit. Legacy
        workspaces retain their historical behavior and return ``None`` unless
        an explicit baseline was supplied while resuming.
        """
        return self._trusted_input_baseline

    def configure_trusted_input_baseline(self, revision: str) -> str:
        """Resolve and install the persisted trusted-input baseline."""
        resolved = self._resolve_trusted_input_baseline(revision)
        self._trusted_input_baseline = resolved
        self._log(f"[git-tracking] trusted input baseline: {resolved[:12]}")
        return resolved

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
            if len(line) > 3  # noqa: PLR2004  # tracked: #288
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
            restore_cmd = [
                "git",
                "restore",
                f"--source={sha}",
                "--staged",
                "--worktree",
                "--",
                ".",
            ]
            if self.mode is GitTrackingMode.USER_PROJECT:
                restore_cmd.extend([":(exclude).vs", ":(exclude).vs/**"])
            self.run(restore_cmd)
            if clean:
                clean_cmd = ["git", "clean", "-fd"]
                if self.mode is GitTrackingMode.USER_PROJECT:
                    clean_cmd.extend(["-e", ".vs/"])
                clean_cmd.extend(["--", "."])
                self.run(clean_cmd, check=False)
            self._restore_preserved_paths(preserved)
            return True  # noqa: TRY300  # tracked: #288
        except Exception as exc:  # noqa: BLE001  # tracked: #288
            try:
                self._restore_preserved_paths(preserved)
            except Exception as preserve_exc:  # noqa: BLE001  # tracked: #288
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
            if relative.is_absolute() or relative == Path() or ".." in relative.parts:
                raise ValueError(f"preserved path must be workspace-relative: {raw_path}")  # noqa: TRY003  # tracked: #288
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
        trusted_pathspecs = self._trusted_input_pathspecs()
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
                    *trusted_pathspecs,
                ]
            )
            commits = baseline.stdout.decode().splitlines()[0:1]
            if not commits:
                return ["unable to resolve the initial workspace commit"]
            initial_commit = commits[0]

        pathspec = ["--", *trusted_pathspecs]
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
            if len(line) > 3  # noqa: PLR2004  # tracked: #288
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
            raise ValueError(f"trusted input baseline {revision!r} is not a commit")  # noqa: TRY003  # tracked: #288
        commit = resolved.stdout.decode(errors="replace").strip()
        ancestor = self.run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
            check=False,
        )
        if ancestor.returncode != 0:
            raise ValueError(f"trusted input baseline {revision!r} is not an ancestor of HEAD")  # noqa: TRY003  # tracked: #288
        return commit

    def _workspace_gitignore(self) -> str:
        """Contents of the workspace ``.gitignore`` (excluded dirs + artifacts)."""
        lines = sorted(self._excluded_dirs) + list(self._ARTIFACT_GITIGNORE_PATTERNS)
        return "\n".join(lines) + "\n"

    # -- in-place user projects ---------------------------------------------

    @property
    def project_branch(self) -> str | None:
        """Return the run branch name selected for project mode."""
        if self.mode is not GitTrackingMode.USER_PROJECT or self.run_id is None:
            return None
        return f"vibesys/{self.run_id}"

    def _init_user_project(
        self,
        *,
        existing: bool,
        trusted_input_baseline: str | None,
    ) -> None:
        """Initialize or resume tracking directly in a user's project root."""
        branch = self._validated_project_branch()
        inside_work_tree = self._prepare_project_repository(existing=existing)
        self._bind_repository()
        self._install_project_excludes()
        self._require_private_inputs_absent_from_history()
        if existing:
            self._resume_user_project(branch, trusted_input_baseline)
            return

        self._start_user_project(
            branch=branch,
            inside_work_tree=inside_work_tree,
            trusted_input_baseline=trusted_input_baseline,
        )

    def _validated_project_branch(self) -> str:
        branch = self.project_branch
        if branch is None:
            raise ValueError("user-project git tracking requires a run id")  # noqa: TRY003  # tracked: #288
        valid = self.run(["git", "check-ref-format", "--branch", branch], check=False)
        if valid.returncode != 0:
            raise ValueError(f"invalid VibeSys run id for a Git branch: {self.run_id!r}")  # noqa: TRY003  # tracked: #288
        return branch

    def _prepare_project_repository(self, *, existing: bool) -> bool:
        inside_work_tree = self._inside_work_tree()
        if not inside_work_tree:
            if existing:
                raise ValueError(  # noqa: TRY003  # tracked: #288
                    f"cannot resume VibeSys run {self.run_id!r}: no Git repository in {self.root}"
                )
            self.run(["git", "init", "-q", "-b", "main"])
            return False

        top_level = self.run(["git", "rev-parse", "--show-toplevel"])
        repository_root = Path(top_level.stdout.decode(errors="replace").strip()).resolve()
        if repository_root != self.root.resolve():
            raise ValueError(  # noqa: TRY003  # tracked: #288
                "user-project git tracking requires the input directory to be "
                f"the repository root; found containing repository {repository_root}"
            )
        return True

    def _resume_user_project(
        self,
        branch: str,
        trusted_input_baseline: str | None,
    ) -> None:
        if not self._branch_exists(branch):
            raise ValueError(  # noqa: TRY003  # tracked: #288
                f"cannot resume VibeSys run {self.run_id!r}: branch {branch!r} does not exist"
            )
        if self._current_branch() != branch:
            self._require_clean_project(
                "cannot switch to the resumed VibeSys branch with pending project changes"
            )
            self.run(["git", "switch", branch])
        if trusted_input_baseline is not None:
            self.configure_trusted_input_baseline(trusted_input_baseline)

    def _start_user_project(
        self,
        *,
        branch: str,
        inside_work_tree: bool,
        trusted_input_baseline: str | None,
    ) -> None:
        if trusted_input_baseline is not None:
            raise ValueError("trusted input baseline is only valid when resuming a run")  # noqa: TRY003  # tracked: #288

        if inside_work_tree:
            if self.current_sha() is None:
                raise ValueError(  # noqa: TRY003  # tracked: #288
                    "existing project repository has no baseline commit"
                )
            self._require_clean_project(
                "existing project repository must be clean before starting a VibeSys run"
            )
        else:
            self._add_all()
            self.run(["git", "commit", "--allow-empty", "-m", "initial: project baseline"])

        branch_point = self.current_sha()
        if branch_point is None:
            raise ValueError("user-project baseline commit could not be resolved")  # noqa: TRY003  # tracked: #288

        if self._branch_exists(branch):
            raise ValueError(f"VibeSys run branch already exists: {branch}")  # noqa: TRY003  # tracked: #288
        self.run(["git", "switch", "-c", branch])
        self._trusted_input_baseline = branch_point
        self._log(f"[git-tracking] trusted input baseline: {branch_point[:12]}")

    def _install_project_excludes(self) -> None:
        """Idempotently add local/private paths to ``.git/info/exclude``."""
        patterns = list(self._PROJECT_LOCAL_EXCLUDE_PATTERNS)
        patterns.extend(
            f"{directory}/" for directory in sorted(self._excluded_dirs) if directory != ".git"
        )
        patterns.extend(self._ARTIFACT_GITIGNORE_PATTERNS)
        self._append_exclude_patterns(patterns)

    def _trusted_input_pathspecs(self) -> tuple[str, ...]:
        return tuple(f":(literal){path.as_posix()}" for path in self._trusted_input_paths)

    def _require_private_inputs_absent_from_history(self) -> None:
        """Reject private root inputs recoverable through reachable Git objects."""
        if self.current_sha() is None:
            return
        objects = self.run(
            ["git", "rev-list", "--objects", "--all", "--reflog"],
            check=False,
        )
        if objects.returncode != 0:
            raise ValueError("cannot inspect project Git history for private inputs")  # noqa: TRY003  # tracked: #288
        private_paths = sorted(
            {
                path
                for line in objects.stdout.decode(errors="replace").splitlines()
                if (path := line.partition(" ")[2]) and self._is_private_project_input(path)
            }
        )
        if private_paths:
            raise ValueError(  # noqa: TRY003  # tracked: #288
                "project Git history contains private inputs that an optimization agent "
                f"could recover: {', '.join(private_paths)}. Remove them from history or "
                "start from a fresh repository."
            )

    @staticmethod
    def _is_private_project_input(path: str) -> bool:
        return path in {".env", "agent.toml"} or path.startswith(".env.")

    def _append_exclude_patterns(self, patterns: Iterable[str]) -> None:
        exclude_file = self._exclude_file
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_file.read_text() if exclude_file.exists() else ""
        have = set(existing.splitlines())
        new = [pattern for pattern in dict.fromkeys(patterns) if pattern not in have]
        if not new:
            return
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        exclude_file.write_text(existing + prefix + "\n".join(new) + "\n")

    def _require_clean_project(self, message: str) -> None:
        changes = self.pending_changes()
        if changes:
            raise ValueError(f"{message}: {', '.join(changes)}")  # noqa: TRY003  # tracked: #288

    def _branch_exists(self, branch: str) -> bool:
        result = self.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            check=False,
        )
        return result.returncode == 0

    def _current_branch(self) -> str | None:
        result = self.run(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.decode(errors="replace").strip()

    def _framework_metadata_destinations(
        self,
        metadata: Mapping[str | Path, str | bytes],
    ) -> list[tuple[Path, Path, str | bytes]]:
        """Validate metadata keys and resolve their safe project destinations."""
        vs_root = self.root / ".vs"
        if vs_root.is_symlink():
            raise ValueError("framework metadata directory must not be a symlink")  # noqa: TRY003  # tracked: #288

        result: list[tuple[Path, Path, str | bytes]] = []
        seen: set[Path] = set()
        for raw_path, content in metadata.items():
            relative = Path(raw_path)
            if (
                relative.is_absolute()
                or relative == Path()
                or ".." in relative.parts
                or relative.parts[:1] != (".vs",)
                or relative.parts[:2] == (".vs", "local")
            ):
                raise ValueError(  # noqa: TRY003  # tracked: #288
                    f"framework metadata path must be below .vs/ but outside .vs/local/: {raw_path}"
                )
            if relative in seen:
                raise ValueError(f"duplicate framework metadata path: {raw_path}")  # noqa: TRY003  # tracked: #288
            seen.add(relative)

            destination = self.root / relative
            for parent in (destination, *destination.parents):
                if parent == self.root:
                    break
                if parent.is_symlink():
                    raise ValueError(  # noqa: TRY003  # tracked: #288
                        f"framework metadata path traverses a symlink: {raw_path}"
                    )
            result.append((relative, destination, content))
        return result

    def _pending_committed_framework_metadata(self) -> list[str]:
        result = self.run(["git", "diff", "--name-only", "HEAD", "--", ".vs"])
        return sorted(path for path in result.stdout.decode(errors="replace").splitlines() if path)

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

        Walks the worktree (skipping Git-ignored runtime/artifact directories,
        never following symlinks) and records files lacking ``R_OK`` and
        directories lacking ``R_OK|X_OK`` (an unsearchable dir hides its whole
        subtree from ``git add`` too). Pruning ignored trees matters because a
        Python/CUDA environment can contain gigabytes and hundreds of thousands
        of files that ``git add`` itself will never inspect.
        """
        unreadable: list[str] = []
        root = str(self.root)
        ignored_dirs = {".git", *self._excluded_dirs}
        ignored_dirs.update(
            pattern.removesuffix("/")
            for pattern in self._ARTIFACT_GITIGNORE_PATTERNS
            if pattern.endswith("/") and not set(pattern).intersection("*?[")
        )
        for dirpath, dirnames, filenames in os.walk(root):
            kept = []
            for d in dirnames:
                if d in ignored_dirs:
                    continue
                full = os.path.join(dirpath, d)  # noqa: PTH118  # tracked: #288
                if os.access(full, os.R_OK | os.X_OK):
                    kept.append(d)
                else:
                    unreadable.append(os.path.relpath(full, root))
            dirnames[:] = kept  # prune unsearchable dirs from the walk
            for f in filenames:
                full = os.path.join(dirpath, f)  # noqa: PTH118  # tracked: #288
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
            paths.append(m.group(1))  # noqa: PERF401  # tracked: #288
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
        shown = ", ".join(new[:5]) + ("…" if len(new) > 5 else "")  # noqa: PLR2004  # tracked: #288
        self._log(f"[git-tracking] excluded {len(new)} unreadable path(s) from snapshot: {shown}")

    def _add_all(self) -> None:
        """``git add -A``, resilient to files the host user cannot read.

        Excludes unreadable paths up front, then retries on any residual
        permission failure (a file may appear between the scan and the add).
        """
        self._exclude_paths(self._collect_unreadable())
        if self.mode is GitTrackingMode.USER_PROJECT and self.current_sha() is not None:
            # Discard any index mutations made by the candidate before staging
            # the exact candidate-owned path set ourselves.
            self.run(["git", "reset", "--quiet", "HEAD", "--", "."])
        add_cmd = ["git", "add", "-A", "--", "."]
        for _ in range(3):
            result = self.run(add_cmd, check=False)
            if result.returncode == 0:
                self._unstage_project_owned_paths()
                return
            stderr = result.stderr.decode(errors="replace")
            offenders = self._unreadable_from_stderr(stderr)
            if not offenders:
                break  # failure unrelated to unreadable files — surface it
            self._exclude_paths(offenders)
        # Final attempt: let run() raise with full diagnostics if it still fails.
        self.run(add_cmd)
        self._unstage_project_owned_paths()

    def _unstage_project_owned_paths(self) -> None:
        """Remove framework, private, and cache paths from the candidate index."""
        if self.mode is not GitTrackingMode.USER_PROJECT:
            return
        protected = [
            ".vs",
            ".env",
            "agent.toml",
        ]
        protected.extend(
            f":(glob)**/{directory}/**"
            for directory in sorted(self._excluded_dirs)
            if directory != ".git"
        )
        for pattern in self._ARTIFACT_GITIGNORE_PATTERNS:
            normalized = pattern.removesuffix("/")
            if pattern.endswith("/"):
                protected.append(f":(glob)**/{normalized}/**")
            else:
                protected.append(f":(glob)**/{normalized}")
        if self.current_sha() is None:
            self.run(["git", "rm", "--cached", "-r", "--ignore-unmatch", "--", *protected])
        else:
            self.run(["git", "reset", "--quiet", "HEAD", "--", *protected])

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
        if self.mode is GitTrackingMode.USER_PROJECT:
            exclude_file = self.run(["git", "rev-parse", "--git-path", "info/exclude"])
            exclude_path = Path(exclude_file.stdout.decode(errors="replace").strip())
            if not exclude_path.is_absolute():
                exclude_path = self.root / exclude_path
            self._exclude_file = exclude_path.resolve()

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
