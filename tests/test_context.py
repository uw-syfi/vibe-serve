import subprocess
import sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vibesys.constants import ComputeBackend
from vibesys.context import (
    _close_after_construction_failure,
    _RunContext,
    create_candidate_context,
    create_run_context,
    setup_exp_dir,
)
from vibesys.domains.base import DomainName
from vibesys.domains.environment import EnvironmentPatch, NoopEnvironmentHooks
from vibesys.errors import ConfigurationError
from vibesys.profilers import ACTIVE_PROFILER_KINDS, ProfilerKind, ProfilerPreflightResult
from vibesys.run import GitTracker, RunLogger, RunPaths, Workspace
from vibesys.sandbox.run_environment import RunEnvironmentSpec


def _minimal_copy_context(workspace):  # noqa: ANN001, ANN202  # tracked: #288
    ctx = object.__new__(_RunContext)
    ctx._paths = RunPaths(  # noqa: SLF001  # tracked: #288
        exp_dir=workspace.parent,
        log_dir=workspace.parent / "logs",
        workspace=workspace,
        run_log_path=workspace.parent / "run.log",
    )
    ctx.git_tracking = True
    ctx.EXCLUDED_WORKSPACE_DIRS = {".git", "target"}
    ctx.run_environment = SimpleNamespace(isolated=False)
    ctx.backend_impl = MagicMock()
    ctx.lprint = MagicMock()
    ctx.git = GitTracker(workspace, log=ctx.lprint, excluded_dirs=ctx.EXCLUDED_WORKSPACE_DIRS)
    ctx.implementer_backend = SimpleNamespace()
    ctx._experiment_repository = None  # noqa: SLF001  # tracked: #288
    return ctx


def test_setup_exp_dir_uses_selected_collection_and_unique_names(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    runs_dir = tmp_path / "selected-runs"

    first = setup_exp_dir("test", runs_dir=runs_dir)
    second = setup_exp_dir("test", runs_dir=runs_dir)

    assert first != second
    assert first.parent == runs_dir
    assert second.parent == runs_dir
    assert first.name.endswith("-test")
    assert second.name.endswith("-test")
    assert (first / ".git").is_dir()
    assert (second / ".git").is_dir()


def test_log_switch_retargets_stderr_tee_and_restores_on_close(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    ctx = object.__new__(_RunContext)
    original_stderr = sys.stderr
    ctx.logger = RunLogger(tmp_path)
    ctx._paths = RunPaths(  # noqa: SLF001  # tracked: #288
        exp_dir=tmp_path,
        log_dir=tmp_path,
        workspace=tmp_path / "workspace",
        run_log_path=ctx.logger.path,
    )
    original_file = ctx.logger.file
    ctx.agent_runner = SimpleNamespace(_run_log_file=ctx.run_log_file)

    ctx.switch_log_file("round001")

    assert original_file.closed
    assert ctx.agent_runner._run_log_file is ctx.run_log_file  # noqa: SLF001  # tracked: #288
    # The unconditional tee mirrors stderr into the *current* log file,
    # stripped of ANSI escapes, while writes still reach the real stderr.
    print("\033[31mcolored diagnostic\033[0m", file=sys.stderr)  # noqa: T201  # tracked: #288
    assert ctx.run_log_path.name.endswith("-round001.log")

    ctx.logger.close()
    assert sys.stderr is original_stderr
    assert "colored diagnostic" in ctx.run_log_path.read_text()
    assert "\033[31m" not in ctx.run_log_path.read_text()


def test_input_copy_respects_source_gitignore(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    source = tmp_path / "source"
    source.mkdir()
    (source / ".gitignore").write_text("/candidate.so\n/build/\n")
    (source / "main.rs").write_text("fn main() {}\n")
    (source / "candidate.so").write_bytes(b"stale")
    (source / "build").mkdir()
    (source / "build" / "cache").write_text("stale")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)  # noqa: S607  # tracked: #288

    destination = tmp_path / "workspace"
    workspace = Workspace(
        destination,
        run_environment=SimpleNamespace(isolated=False),  # pyright: ignore[reportArgumentType]  # tracked: #297
        backend=MagicMock(),
        log=MagicMock(),
        project_root=tmp_path,
        excluded_dirs={".git", "target"},
    )
    workspace.copy_dir(source, destination, respect_source_gitignore=True)

    assert (destination / "main.rs").is_file()
    assert not (destination / "candidate.so").exists()
    assert not (destination / "build").exists()


def test_trusted_input_changes_compare_against_initial_commit(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    workspace = tmp_path / "workspace"
    (workspace / "accuracy_checker").mkdir(parents=True)
    (workspace / "accuracy_checker" / "checker.py").write_text("print('ok')\n")
    (workspace / "main.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)  # noqa: S607  # tracked: #288
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)  # noqa: S607  # tracked: #288
    subprocess.run(
        [  # noqa: S607  # tracked: #288
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=workspace,
        check=True,
    )

    ctx = _minimal_copy_context(workspace)
    (workspace / "main.py").write_text("VALUE = 2\n")
    assert ctx.trusted_input_changes() == []

    (workspace / "accuracy_checker" / "checker.py").write_text("print('forged')\n")
    assert ctx.trusted_input_changes() == ["accuracy_checker/checker.py"]


def test_workspace_snapshot_pushes_remote_experiment_checkpoint(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("VALUE = 1\n")
    ctx = _minimal_copy_context(workspace)
    ctx.git.init(existing=False)
    ctx._experiment_repository = MagicMock()  # noqa: SLF001  # tracked: #288

    (workspace / "main.py").write_text("VALUE = 2\n")
    ctx.snapshot_workspace("round-1-implementer")

    ctx._experiment_repository.sync.assert_called_once_with()  # noqa: SLF001  # tracked: #288


def test_directory_snapshot_pushes_remote_experiment_checkpoint(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("VALUE = 1\n")
    ctx = _minimal_copy_context(workspace)
    ctx.git_tracking = False
    ctx.workspace_files = MagicMock()
    ctx._experiment_repository = MagicMock()  # noqa: SLF001  # tracked: #288

    ctx.snapshot_workspace("round-1-implementer")

    snapshot = ctx.log_dir / "snapshots" / "round-1-implementer"
    assert (snapshot / "main.py").read_text() == "VALUE = 1\n"
    ctx.workspace_files.replace_external_symlinks.assert_called_once_with(snapshot)
    ctx._experiment_repository.sync.assert_called_once_with()  # noqa: SLF001  # tracked: #288


def test_workspace_snapshot_retries_remote_failure_without_stopping_run(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("VALUE = 1\n")
    ctx = _minimal_copy_context(workspace)
    ctx.git.init(existing=False)
    ctx._experiment_repository = MagicMock()  # noqa: SLF001  # tracked: #288
    ctx._experiment_repository.sync.side_effect = RuntimeError("network unavailable")  # noqa: SLF001  # tracked: #288

    ctx.snapshot_workspace("round-1-implementer")

    ctx._experiment_repository.sync.assert_called_once_with()  # noqa: SLF001  # tracked: #288
    ctx.lprint.assert_called_with(
        "[warn] experiment repository checkpoint push failed: network unavailable"
    )


class _FakeBackend:
    image = "fake-image"
    selected_device = None

    def __init__(self, profiler_kind=None) -> None:  # noqa: ANN001  # tracked: #288
        self.sandbox = MagicMock()
        if profiler_kind is not None:
            self.profiler_kind = profiler_kind

    def make_sandbox(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202  # tracked: #288
        return self.sandbox

    def make_monitor(self, _log_dir):  # noqa: ANN001, ANN202  # tracked: #288
        return None


@pytest.fixture(autouse=True)
def _native_profiler_preflight_ok(monkeypatch):  # pyright: ignore[reportUnusedFunction] -- autouse fixture, never referenced by name  # noqa: ANN001, ANN202  # tracked: #288
    monkeypatch.setattr(
        "vibesys.context.preflight_profiler_kind",
        lambda kind: ProfilerPreflightResult(kind, True),  # noqa: FBT003  # tracked: #288
    )


def _write_ref(tmp_path):  # noqa: ANN001, ANN202  # tracked: #288
    ref_dir = tmp_path / "input"
    ref_dir.mkdir()
    ref = ref_dir / "reference.py"
    ref.write_text("pass\n")
    return ref


def _write_support_dirs(project_root):  # noqa: ANN001, ANN202  # tracked: #288
    dirs = {
        ProfilerKind.NSYS: "nsys_profiler",
        ProfilerKind.OTEL: "otel_profiler",
        ProfilerKind.TORCH: "torch_profiler",
        ProfilerKind.NEURON: "neuron_profiler",
        ProfilerKind.MACOS_CPU: "macos_cpu_profiler",
        ProfilerKind.LINUX_CPU: "linux_cpu_profiler",
    }
    for kind in dirs:
        source_dir = project_root / "resources" / "profilers" / kind.value
        source_dir.mkdir(parents=True)
        (source_dir / "server.py").write_text("pass\n")
    return {kind: str(project_root / "resources" / "profilers" / kind.value) for kind in dirs}


@pytest.mark.parametrize(
    ("profiler_kind", "workspace_name", "profiler_domain"),
    [
        (ProfilerKind.TORCH, "torch_profiler", DomainName.LLM_SERVING),
        (ProfilerKind.NEURON, "neuron_profiler", DomainName.LLM_SERVING),
        (ProfilerKind.OTEL, "otel_profiler", DomainName.MICROSERVICES),
    ],
)
def test_run_context_defaults_profiler_support_paths(  # noqa: ANN201  # tracked: #288
    tmp_path,  # noqa: ANN001  # tracked: #288
    profiler_kind,  # noqa: ANN001  # tracked: #288
    workspace_name,  # noqa: ANN001  # tracked: #288
    profiler_domain,  # noqa: ANN001  # tracked: #288
):
    project_root = tmp_path / "project"
    source_dir = project_root / "resources" / "profilers" / profiler_kind.value
    source_dir.mkdir(parents=True)
    (source_dir / "server.py").write_text("pass\n")

    ref = _write_ref(tmp_path)

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend()),
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name=f"{profiler_kind}-defaults",
            runs_dir=project_root / "exp_env",
            input_path=str(ref.parent),
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
            profiler_kind=profiler_kind,
            profiler_domain=profiler_domain,
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
        ) as ctx,
    ):
        assert ctx.profiler_support_path == str(source_dir)
        assert (ctx.workspace / workspace_name / "server.py").is_file()


def test_cli_context_skips_unused_langchain_model_construction(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    ref = _write_ref(tmp_path)

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model") as build_model,
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend()),
        create_run_context(
            config={  # pyright: ignore[reportArgumentType]  # tracked: #297
                "model": {"name": "gpt-5.6-sol"},
                "thinking": {"level": "xhigh"},
                "agent": {"backend": "cli", "cli_provider": "codex"},
            },
            exp_name="cli-reasoning",
            runs_dir=project_root / "exp_env",
            input_path=str(ref.parent),
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
            profiler_kind=ProfilerKind.NONE,
            profiler_domain=DomainName.LLM_SERVING,
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
        ) as ctx,
    ):
        assert ctx.model is None
        build_model.assert_not_called()


@pytest.mark.parametrize(
    "selected",
    [ProfilerKind.NONE, *sorted(ACTIVE_PROFILER_KINDS, key=lambda kind: kind.value)],
)
def test_run_context_copies_only_selected_profiler_support(tmp_path, selected):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    _write_support_dirs(project_root)
    ref = _write_ref(tmp_path)

    if selected in {ProfilerKind.MACOS_CPU, ProfilerKind.LINUX_CPU}:
        domain = DomainName.GENERIC
    elif selected is ProfilerKind.OTEL:
        domain = DomainName.MICROSERVICES
    else:
        domain = DomainName.LLM_SERVING
    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend()),
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name=f"{selected.value}-support",
            runs_dir=project_root / "exp_env",
            input_path=str(ref.parent),
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
            profiler_kind=selected,
            profiler_domain=domain,
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
        ) as ctx,
    ):
        expected = {
            ProfilerKind.NSYS: selected is ProfilerKind.NSYS,
            ProfilerKind.OTEL: selected is ProfilerKind.OTEL,
            ProfilerKind.TORCH: selected is ProfilerKind.TORCH,
            ProfilerKind.NEURON: selected is ProfilerKind.NEURON,
            ProfilerKind.MACOS_CPU: selected is ProfilerKind.MACOS_CPU,
            ProfilerKind.LINUX_CPU: selected is ProfilerKind.LINUX_CPU,
        }
        assert ctx.profiler_kind is selected
        assert (ctx.workspace / "nsys_profiler").exists() is expected[ProfilerKind.NSYS]
        assert (ctx.workspace / "otel_profiler").exists() is expected[ProfilerKind.OTEL]
        assert (ctx.workspace / "torch_profiler").exists() is expected[ProfilerKind.TORCH]
        assert (ctx.workspace / "neuron_profiler").exists() is expected[ProfilerKind.NEURON]
        assert (ctx.workspace / "macos_cpu_profiler").exists() is expected[ProfilerKind.MACOS_CPU]
        assert (ctx.workspace / "linux_cpu_profiler").exists() is expected[ProfilerKind.LINUX_CPU]


def test_run_context_generic_auto_resolves_to_macos_profiler(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    support_paths = _write_support_dirs(project_root)
    ref = _write_ref(tmp_path)

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend(ProfilerKind.NSYS)),
        patch("vibesys.profilers.platform.system", return_value="Darwin"),
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name="generic-auto-none",
            runs_dir=project_root / "exp_env",
            input_path=str(ref.parent),
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
            profiler_kind=ProfilerKind.AUTO,
            profiler_domain=DomainName.GENERIC,
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
            environment_hooks=NoopEnvironmentHooks(),
        ) as ctx,
    ):
        assert ctx.profiler_kind is ProfilerKind.MACOS_CPU
        assert ctx.profiler_support_path == support_paths[ProfilerKind.MACOS_CPU]
        assert not (ctx.workspace / "nsys_profiler").exists()
        assert not (ctx.workspace / "torch_profiler").exists()
        assert not (ctx.workspace / "neuron_profiler").exists()
        assert (ctx.workspace / "macos_cpu_profiler").exists()
        assert not (ctx.workspace / "linux_cpu_profiler").exists()


def test_run_context_generic_auto_resolves_to_linux_profiler(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    support_paths = _write_support_dirs(project_root)
    ref = _write_ref(tmp_path)

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend(ProfilerKind.NSYS)),
        patch("vibesys.profilers.platform.system", return_value="Linux"),
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name="generic-auto-linux",
            runs_dir=project_root / "exp_env",
            input_path=str(ref.parent),
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
            profiler_kind=ProfilerKind.AUTO,
            profiler_domain=DomainName.GENERIC,
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
            environment_hooks=NoopEnvironmentHooks(),
        ) as ctx,
    ):
        assert ctx.profiler_kind is ProfilerKind.LINUX_CPU
        assert ctx.profiler_support_path == support_paths[ProfilerKind.LINUX_CPU]
        assert not (ctx.workspace / "nsys_profiler").exists()
        assert not (ctx.workspace / "torch_profiler").exists()
        assert not (ctx.workspace / "neuron_profiler").exists()
        assert not (ctx.workspace / "macos_cpu_profiler").exists()
        assert (ctx.workspace / "linux_cpu_profiler").exists()


def test_run_context_fails_fast_when_resolved_profiler_is_unusable(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    _write_support_dirs(project_root)
    ref = _write_ref(tmp_path)

    def fail_preflight(kind):  # noqa: ANN001, ANN202  # tracked: #288
        return ProfilerPreflightResult(
            kind,
            False,  # noqa: FBT003  # tracked: #288
            ("perf_unavailable", "perf_event_paranoid_restrictive"),
            ("perf_path=missing", "perf_event_paranoid=3"),
        )

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend(ProfilerKind.NSYS)),
        patch("vibesys.profilers.platform.system", return_value="Linux"),
        patch("vibesys.context.preflight_profiler_kind", side_effect=fail_preflight),
        pytest.raises(ConfigurationError, match="Resolved profiler 'linux_cpu' is not usable"),
    ):
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name="generic-auto-linux-unusable",
            runs_dir=project_root / "exp_env",
            input_path=str(ref.parent),
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
            profiler_kind=ProfilerKind.AUTO,
            profiler_domain=DomainName.GENERIC,
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
            environment_hooks=NoopEnvironmentHooks(),
        )


@pytest.mark.parametrize(
    "profiler_kind",
    sorted(
        ACTIVE_PROFILER_KINDS - {ProfilerKind.MACOS_CPU, ProfilerKind.LINUX_CPU},
        key=lambda kind: kind.value,
    ),
)
def test_run_context_rejects_generic_explicit_active_profilers(tmp_path, profiler_kind):  # noqa: ANN001, ANN201  # tracked: #288
    ref = _write_ref(tmp_path)

    with (
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend(profiler_kind)),
        pytest.raises(ValueError, match="not supported for domain 'generic'"),
    ):
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name=f"generic-{profiler_kind.value}",
            runs_dir=tmp_path / "exp_env",
            input_path=str(ref.parent),
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
            profiler_kind=profiler_kind,
            profiler_domain=DomainName.GENERIC,
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
            environment_hooks=NoopEnvironmentHooks(),
        )


def test_run_context_llm_auto_uses_backend_profiler_and_defaults_support_dir(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    support_paths = _write_support_dirs(project_root)
    ref = _write_ref(tmp_path)

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend(ProfilerKind.NSYS)),
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name="llm-auto-nsys",
            runs_dir=project_root / "exp_env",
            input_path=str(ref.parent),
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
            profiler_kind=ProfilerKind.AUTO,
            profiler_domain=DomainName.LLM_SERVING,
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
        ) as ctx,
    ):
        assert ctx.profiler_kind is ProfilerKind.NSYS
        assert ctx.profiler_support_path == support_paths[ProfilerKind.NSYS]
        assert (ctx.workspace / "nsys_profiler" / "server.py").is_file()
        assert not (ctx.workspace / "torch_profiler").exists()
        assert not (ctx.workspace / "neuron_profiler").exists()


def test_run_context_noop_environment_hooks_do_not_require_model_artifacts(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    ref_dir = tmp_path / "queue" / "reference"
    ref_dir.mkdir(parents=True)
    (ref_dir / "reference.py").write_text("pass\n")

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend()),
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name="generic-reference-dir",
            runs_dir=project_root / "exp_env",
            input_path=str(ref_dir.parent),
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
            environment_hooks=NoopEnvironmentHooks(),
        ) as ctx,
    ):
        assert (ctx.workspace / "reference" / "reference.py").is_file()
        assert not (ref_dir / "model").exists()


def test_candidate_context_cleans_up_when_agent_runner_construction_fails(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    workspace = tmp_path / "candidates" / f"{tmp_path.name}-g1c2" / "workspace"
    parent = SimpleNamespace(
        exp_dir=tmp_path,
        git=MagicMock(),
        run_environment=MagicMock(),
        backend=ComputeBackend.CUDA,
        backend_impl=_FakeBackend(),
        EXCLUDED_WORKSPACE_DIRS={".git", "target"},
        accuracy_command="check-accuracy",
        benchmark_command="run-benchmark",
        profiler_support_path=None,
        profiler_support_name=None,
        environment_patch=SimpleNamespace(bind_mounts=()),
        skill_source_paths=[],
        model="mock-model",
        model_name="claude-sonnet-4-6",
        workspace_sources=(),
    )
    parent.git.add_worktree.side_effect = lambda path, _commit: path.mkdir(parents=True)
    session = MagicMock()
    session.__enter__.return_value = session
    session.view = SimpleNamespace(
        paths=SimpleNamespace(
            accuracy_command="check-accuracy",
            benchmark_command="run-benchmark",
            profiler_support=None,
        ),
        cli_sandboxed=False,
    )
    session.sandbox = MagicMock()
    parent.run_environment.open.return_value = session

    with (
        patch("vibesys.context.build_agent_runner", side_effect=SystemExit("boom")),
        pytest.raises(SystemExit, match="boom"),
    ):
        create_candidate_context(
            parent,  # pyright: ignore[reportArgumentType]  # tracked: #297
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            generation=1,
            child_idx=2,
            parent_commit="deadbeef",
        )

    session.__exit__.assert_called_once()
    parent.git.remove_worktree.assert_called_once_with(workspace)


def test_candidate_context_cleans_up_when_add_worktree_partially_fails(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    workspace = tmp_path / "candidates" / f"{tmp_path.name}-g1c2" / "workspace"
    parent = SimpleNamespace(exp_dir=tmp_path, git=MagicMock())

    def partially_add(path, _commit):  # noqa: ANN001, ANN202  # tracked: #288
        path.mkdir(parents=True)
        raise RuntimeError("git add failed")  # noqa: TRY003  # tracked: #288

    parent.git.add_worktree.side_effect = partially_add

    with pytest.raises(RuntimeError, match="git add failed"):
        create_candidate_context(
            parent,  # pyright: ignore[reportArgumentType]  # tracked: #297
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            generation=1,
            child_idx=2,
            parent_commit="deadbeef",
        )

    parent.git.remove_worktree.assert_called_once_with(workspace)


def test_run_context_cleans_up_when_agent_runner_construction_fails(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    ref = _write_ref(tmp_path)
    hooks = MagicMock()
    hooks.prepare.return_value = EnvironmentPatch()
    original_stderr = sys.stderr

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", side_effect=RuntimeError("boom")),
        patch("vibesys.context.backends.get", return_value=_FakeBackend()),
        pytest.raises(RuntimeError, match="boom"),
    ):
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name="failed-construction",
            runs_dir=project_root / "exp_env",
            input_path=str(ref.parent),
            accuracy_command="check-accuracy",
            benchmark_command="run-benchmark",
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
            environment_hooks=hooks,
        )

    assert sys.stderr is original_stderr
    hooks.teardown.assert_called_once()


def test_run_context_tears_down_prepared_hooks_when_workspace_setup_fails(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    ref = _write_ref(tmp_path)
    hooks = MagicMock()
    hooks.prepare.return_value = EnvironmentPatch()

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.backends.get", return_value=_FakeBackend()),
        patch("vibesys.context.Workspace.setup", side_effect=RuntimeError("setup failed")),
        pytest.raises(RuntimeError, match="setup failed"),
    ):
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name="failed-workspace-setup",
            runs_dir=project_root / "exp_env",
            input_path=str(ref.parent),
            accuracy_command="check-accuracy",
            benchmark_command="run-benchmark",
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
            environment_hooks=hooks,
        )

    hooks.teardown.assert_called_once()


def test_partial_construction_cleanup_does_not_replace_original_error():  # noqa: ANN201  # tracked: #288
    stack = ExitStack()

    def fail_cleanup() -> None:
        raise OSError("cleanup failed")  # noqa: TRY003  # tracked: #288

    stack.callback(fail_cleanup)
    construction_error = RuntimeError("construction failed")

    _close_after_construction_failure(stack, construction_error)

    assert construction_error.__notes__ == [
        "Additional error while cleaning up partial context construction: OSError: cleanup failed"
    ]


def test_run_context_materializes_input_project_path_dependencies(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    input_core = project_root / "sdk" / "queue-input-core"
    input_core.mkdir(parents=True)
    (input_core / "pyproject.toml").write_text(
        "[project]\nname = 'queue-input-core'\nversion = '0.1.0'\n"
    )
    (input_core / "core.py").write_text("VALUE = 1\n")

    input_dir = project_root / "examples" / "data-structures" / "queue-spsc"
    ref_dir = input_dir / "reference"
    acc_dir = input_dir / "accuracy_checker"
    bench_dir = input_dir / "benchmark"
    ref_dir.mkdir(parents=True)
    acc_dir.mkdir()
    bench_dir.mkdir()
    (ref_dir / "reference.py").write_text("pass\n")
    (acc_dir / "checker.py").write_text("pass\n")
    (bench_dir / "benchmark.py").write_text("pass\n")
    (input_dir / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'queue-spsc-input'\n"
        "version = '0.1.0'\n"
        "dependencies = ['queue-input-core']\n"
        "\n"
        "[tool.uv.sources]\n"
        "queue-input-core = { path = '../../../sdk/queue-input-core', editable = true }\n"
    )

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend()),
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name="input-local-package",
            runs_dir=project_root / "exp_env",
            input_path=str(ref_dir.parent),
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
            environment_hooks=NoopEnvironmentHooks(),
        ) as ctx,
    ):
        assert (ctx.workspace / "reference" / "reference.py").is_file()
        assert (ctx.workspace / "accuracy_checker" / "checker.py").is_file()
        assert (ctx.workspace / "benchmark" / "benchmark.py").is_file()
        assert (ctx.workspace / "_input_libs" / "queue-input-core" / "core.py").is_file()
        assert (
            "queue-input-core = { path = '_input_libs/queue-input-core', editable = true }\n"
            in (ctx.workspace / "pyproject.toml").read_text()
        )


def test_run_context_materializes_sdk_deps_from_workspace_seed(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    project_root = tmp_path / "project"
    sdk_pkg = project_root / "sdk" / "vs-bench"
    sdk_pkg.mkdir(parents=True)
    (sdk_pkg / "pyproject.toml").write_text("[project]\nname = 'vs-bench'\nversion = '0.1.0'\n")
    (sdk_pkg / "bench.py").write_text("VALUE = 1\n")

    starter = project_root / "examples" / "starters" / "test-starter"
    starter.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=starter, check=True)  # noqa: S607  # tracked: #288
    (starter / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'test-starter'\n"
        "version = '0.1.0'\n"
        "dependencies = ['vs-bench']\n"
        "\n"
        "[tool.uv]\n"
        "package = false\n"
        "\n"
        "[tool.uv.sources]\n"
        "vs-bench = { path = '../../../sdk/vs-bench' }\n"
    )

    input_dir = project_root / "examples" / "model-serving" / "test-bundle"
    ref_dir = input_dir / "reference"
    bench_dir = input_dir / "benchmark"
    ref_dir.mkdir(parents=True)
    bench_dir.mkdir()
    (ref_dir / "server.py").write_text("pass\n")
    (bench_dir / "benchmark.py").write_text("pass\n")

    with (
        patch("vibesys.context.PROJECT_ROOT", project_root),
        patch("vibesys.resource_paths.PROJECT_ROOT", project_root),
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.context.build_agent_runner", return_value=MagicMock()),
        patch("vibesys.context.backends.get", return_value=_FakeBackend()),
        create_run_context(
            config={"model": {"name": "claude-sonnet-4-6"}},  # pyright: ignore[reportArgumentType]  # tracked: #297
            exp_name="seed-sdk-dep",
            runs_dir=project_root / "exp_env",
            input_path=str(input_dir),
            accuracy_command="echo ok",
            benchmark_command="uv run python benchmark/benchmark.py",
            skills_dirs=[],
            run_environment=RunEnvironmentSpec("local"),
            environment_hooks=NoopEnvironmentHooks(),
            workspace_seed=starter,
        ) as ctx,
    ):
        assert (ctx.workspace / "_input_libs" / "vs-bench" / "bench.py").is_file()
        ws_pyproject = (ctx.workspace / "pyproject.toml").read_text()
        assert "vs-bench = { path = '_input_libs/vs-bench' }" in ws_pyproject
