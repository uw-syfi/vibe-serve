import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vibesys.context import _RunContext, create_candidate_context, create_run_context
from vibesys.domains.base import DomainName
from vibesys.domains.environment import EnvironmentPatch, NoopEnvironmentHooks
from vibesys.errors import ConfigurationError
from vibesys.loops.agent.model import ActiveHypothesis
from vibesys.profilers import ProfilerKind, ProfilerPreflightResult
from vibesys.run import RunLogger, RunPaths, RunStateNamespace
from vibesys.sandbox.run_environment import RunEnvironmentSpec
from vs_loop_state import PlainLoopCursor
from vs_project_state import AgentRunConfiguration, ProjectStore


class _FakeBackend:
    image = "fake-image"
    selected_device = None

    def __init__(self) -> None:
        self.sandbox = MagicMock()

    def make_sandbox(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        return self.sandbox

    def make_monitor(self, _log_dir):  # noqa: ANN001, ANN202
        return None


class _RecordingHooks:
    def __init__(self) -> None:
        self.prepared = 0
        self.torn_down = 0

    def prepare(self, _ctx):  # noqa: ANN001, ANN202
        self.prepared += 1
        return EnvironmentPatch()

    def teardown(self, _ctx):  # noqa: ANN001, ANN202
        self.torn_down += 1


@pytest.fixture(autouse=True)
def _context_dependencies(monkeypatch):  # pyright: ignore[reportUnusedFunction]  # noqa: ANN001, ANN202
    monkeypatch.setattr("vibesys.context.backends.get", lambda *_args, **_kwargs: _FakeBackend())
    monkeypatch.setattr("vibesys.context.build_agent_runner", lambda *_args, **_kwargs: MagicMock())
    monkeypatch.setattr(
        "vibesys.context.preflight_profiler_kind",
        lambda kind: ProfilerPreflightResult(kind, True),  # noqa: FBT003
    )


def _configuration(max_rounds: int = 1) -> AgentRunConfiguration:
    return AgentRunConfiguration(
        outer_loop="agent",
        inner_loop="multi-agent",
        interface="inprocess",
        model="gpt-test",
        agent_backend="stub",
        compute_backend="cpu",
        profiler="none",
        max_rounds=max_rounds,
        max_retries_per_round=1,
        judge_every=1,
        official_eval_every=1,
        memory_layout="files",
    )


def _write_project(root: Path, *, evaluator_name: str = "checker") -> Path:
    root.mkdir()
    (root / "OBJECTIVE.md").write_text("Make the queue faster.\n")
    evaluator = root / evaluator_name
    evaluator.mkdir()
    (evaluator / "check.py").write_text("print('ok')\n")
    (root / "queue.py").write_text("VALUE = 1\n")
    (root / "vibesys.input.toml").write_text(
        """\
version = 1

[agent]
domain = "generic"

[accuracy]
command = ["python", "_evaluator/checker/check.py"]

[benchmark]
command = ["python", "_evaluator/checker/check.py"]

[evaluator]
source = "checker"
"""
    )
    return evaluator


def _create_context(  # noqa: PLR0913
    project: Path,
    *,
    runs_dir: Path | None = None,
    evaluator: Path | None = None,
    exp_name: str = "queue",
    existing: bool = False,
    configuration: AgentRunConfiguration | None = None,
    objective: str = "Make the queue faster.\n",
    hooks=None,  # noqa: ANN001
) -> _RunContext:
    return create_run_context(
        config={"model": {"name": "gpt-test"}},  # pyright: ignore[reportArgumentType]
        exp_name=exp_name,
        runs_dir=runs_dir,
        input_path=str(project),
        accuracy_command="python _evaluator/checker/check.py",
        benchmark_command="python _evaluator/checker/check.py",
        evaluator_path=evaluator,
        objective=objective,
        existing=existing,
        project_configuration=configuration or _configuration(),
        profiler_kind=ProfilerKind.NONE,
        profiler_domain=DomainName.GENERIC,
        run_environment=RunEnvironmentSpec("local"),
        agent_backend="stub",
        environment_hooks=hooks or NoopEnvironmentHooks(),
        active_state_model_type=ActiveHypothesis,
    )


def _git(project: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_direct_run_uses_one_project_root_and_canonical_state(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)

    with patch("vibesys.context.build_agent_runner", return_value=MagicMock()) as build_runner:
        with _create_context(project, evaluator=evaluator) as ctx:
            assert ctx.project_root == project
            assert ctx.workspace == project
            assert ctx.project_store.project_root == project
            assert ctx.log_dir == project / ".vs" / "local" / "runs" / ctx.run_id / "logs"
            assert (
                ctx.state.local(RunStateNamespace.AGENT).project_relative_path("active.json")
                == Path(".vs/local/runs") / ctx.run_id / "agent" / "active.json"
            )
            objective_path = Path(ctx.objective_location)
            assert objective_path == (
                project / ".vs" / "runs" / ctx.run_id / "runtime" / "effective-objective.md"
            )
            assert objective_path.read_text() == "Make the queue faster.\n"
            assert objective_path.is_relative_to(ctx.workspace)

        policy = build_runner.call_args.kwargs["project_path_policy"]
        assert Path(".vs") in policy.read_only_paths
        assert Path(".vs/local") in policy.hidden_paths

    manifest = ProjectStore(project).load_run(ctx.run_id)
    assert manifest.branch == f"vibesys/{ctx.run_id}"
    assert _git(project, "branch", "--show-current") == manifest.branch
    assert _git(project, "status", "--porcelain") == ""


def test_copied_run_provisions_self_contained_project_in_collection(tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "input"
    evaluator = _write_project(source)
    runs_dir = tmp_path / "runs"

    with _create_context(source, runs_dir=runs_dir, evaluator=evaluator) as ctx:
        project = ctx.project_root
        assert project.parent == runs_dir
        assert project.name == ctx.run_id
        assert ctx.workspace == project
        assert (project / "queue.py").is_file()
        assert not (project / "checker").exists()
        assert (project / "_evaluator" / "checker" / "check.py").is_file()
        manifest_text = (project / "vibesys.input.toml").read_text()
        assert 'source = "_evaluator/checker"' in manifest_text
        assert "[workspace]" not in manifest_text
        assert ctx.log_dir == project / ".vs" / "local" / "runs" / ctx.run_id / "logs"

    assert _git(project, "status", "--porcelain") == ""


def test_resume_reuses_project_and_run_id_and_only_increases_limit(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    with _create_context(project, evaluator=evaluator) as first:
        run_id = first.run_id

    with _create_context(
        project,
        evaluator=evaluator,
        exp_name=run_id,
        existing=True,
        configuration=_configuration(max_rounds=2),
    ) as resumed:
        assert resumed.project_root == project
        assert resumed.run_id == run_id

    stored = ProjectStore(project).load_run(run_id)
    assert stored.configuration.max_rounds == 2
    assert _git(project, "branch", "--show-current") == f"vibesys/{run_id}"


def test_collection_resume_pushes_existing_origin_on_teardown(tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "input"
    evaluator = _write_project(source)
    runs_dir = tmp_path / "runs"
    with _create_context(source, runs_dir=runs_dir, evaluator=evaluator) as first:
        project = first.project_root
        run_id = first.run_id

    remote = tmp_path / "remote.git"
    subprocess.run(  # noqa: S603
        ["git", "init", "--bare", "-q", str(remote)],  # noqa: S607
        check=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "remote", "add", "origin", str(remote)],  # noqa: S607
        cwd=project,
        check=True,
    )

    with _create_context(
        project,
        runs_dir=runs_dir,
        evaluator=project / "_evaluator" / "checker",
        exp_name=run_id,
        existing=True,
    ):
        pass

    branch = subprocess.run(  # noqa: S603
        ["git", "--git-dir", str(remote), "branch", "--list", f"vibesys/{run_id}"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert f"vibesys/{run_id}" in branch


def test_direct_run_rejects_unmaterialized_starter_source(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)
    seed = tmp_path / "seed"
    seed.mkdir()

    with pytest.raises(ConfigurationError, match="pass --runs-dir"):
        create_run_context(
            config={"model": {"name": "gpt-test"}},  # pyright: ignore[reportArgumentType]
            exp_name="queue",
            runs_dir=None,
            input_path=str(project),
            accuracy_command="true",
            benchmark_command="true",
            workspace_seed=seed,
            evaluator_path=evaluator,
            project_configuration=_configuration(),
            profiler_kind=ProfilerKind.NONE,
            profiler_domain=DomainName.GENERIC,
            run_environment=RunEnvironmentSpec("local"),
            agent_backend="stub",
        )


def test_portable_state_snapshot_replaces_namespace_exactly(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)

    with _create_context(project, evaluator=evaluator) as ctx:
        state = ctx.state.portable(RunStateNamespace.EVOLVE)
        state.save("old.json", PlainLoopCursor(round_idx=1))
        ctx.state.commit("state 1", state)

        state.delete("old.json")
        state.save("new.json", PlainLoopCursor(round_idx=2))
        ctx.state.commit("state 2", state)

        assert ctx.state.local(RunStateNamespace.EVOLVE).project_relative_path() == (
            Path(".vs/local/runs") / ctx.run_id / "evolve"
        )

    tree = _git(project, "ls-tree", "-r", "--name-only", "HEAD")
    assert f".vs/runs/{ctx.run_id}/evolve/new.json" in tree
    assert f".vs/runs/{ctx.run_id}/evolve/old.json" not in tree


def test_candidate_context_uses_project_store_worktree_directory(tmp_path):  # noqa: ANN001, ANN201
    project = tmp_path / "queue"
    evaluator = _write_project(project)

    with _create_context(project, evaluator=evaluator) as parent:
        parent_commit = parent.git.current_sha()
        assert parent_commit is not None
        candidate = create_candidate_context(
            parent,
            config={"model": {"name": "gpt-test"}},  # pyright: ignore[reportArgumentType]
            generation=2,
            child_idx=3,
            parent_commit=parent_commit,
            agent_backend="stub",
        )
        candidate_root = candidate.workspace
        assert candidate_root == (
            parent.project_store.worktrees_dir(parent.run_id) / "g2c3" / "workspace"
        )
        assert candidate.log_dir == (
            parent.state.local(RunStateNamespace.EVOLVE).external_directory("candidates/g2c3/logs")
        )
        assert Path(candidate.objective_location).read_text() == parent.effective_objective
        candidate.close()
        assert not candidate_root.exists()


def test_construction_failure_removes_new_copy_and_tears_down_hooks(tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "input"
    evaluator = _write_project(source)
    runs_dir = tmp_path / "runs"
    hooks = _RecordingHooks()

    with (
        patch("vibesys.context.build_agent_runner", side_effect=RuntimeError("runner failed")),
        pytest.raises(RuntimeError, match="runner failed"),
    ):
        _create_context(source, runs_dir=runs_dir, evaluator=evaluator, hooks=hooks)

    assert hooks.prepared == 1
    assert hooks.torn_down == 1
    assert not runs_dir.exists() or not list(runs_dir.iterdir())


def test_hook_teardown_runs_when_provisioning_fails(tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "input"
    evaluator = _write_project(source)
    hooks = _RecordingHooks()

    with (
        patch("vibesys.context.provision_project", side_effect=RuntimeError("copy failed")),
        pytest.raises(RuntimeError, match="copy failed"),
    ):
        _create_context(source, runs_dir=tmp_path / "runs", evaluator=evaluator, hooks=hooks)

    assert hooks.prepared == 1
    assert hooks.torn_down == 1


def test_log_switch_retargets_stderr_tee(tmp_path):  # noqa: ANN001, ANN201
    ctx = object.__new__(_RunContext)
    original_stderr = sys.stderr
    ctx.logger = RunLogger(tmp_path)
    ctx._paths = RunPaths(  # noqa: SLF001
        project_root=tmp_path,
        log_dir=tmp_path,
        run_log_path=ctx.logger.path,
    )
    original_file = ctx.logger.file
    ctx.agent_runner = SimpleNamespace(_run_log_file=ctx.run_log_file)

    ctx.switch_log_file("round001")

    assert original_file.closed
    print("\033[31mcolored diagnostic\033[0m", file=sys.stderr)  # noqa: T201
    ctx.logger.close()
    assert sys.stderr is original_stderr
    assert "colored diagnostic" in ctx.run_log_path.read_text()
    assert "\033[31m" not in ctx.run_log_path.read_text()
