from __future__ import annotations

import json
import shlex
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibesys.agents import cli_docker
from vibesys.agents.cli_docker import DockerAuthPath
from vibesys.backends import SandboxKind
from vibesys.domains.environment import EnvironmentBindMount
from vibesys.evaluators import EvaluatorPackageRequirement, resolve_evaluator_package
from vibesys.input_manifest import load_project_task
from vibesys.sandbox.run_environment import (
    RunEnvironmentRequest,
    RunEnvironmentSpec,
    build_run_environment,
    make_run_environment_spec,
)
from vs_project_layout import ProjectLayout
from vs_sandbox import ProjectPathPolicy


class FakeBackend:
    image = "fake-image"

    def __init__(self) -> None:
        self.sandbox = MagicMock()
        self.calls = []

    def make_sandbox(self, kind, **kwargs):  # noqa: ANN001, ANN003, ANN201  # tracked: #288
        self.calls.append((kind, kwargs))
        return self.sandbox


def _request(tmp_path: Path, backend: FakeBackend, **overrides):  # noqa: ANN003, ANN202  # tracked: #288
    workspace = overrides.pop("workspace", tmp_path / "workspace")
    workspace.mkdir(exist_ok=True)
    values = dict(  # noqa: C408  # tracked: #288
        log_dir=tmp_path / "logs",
        workspace=workspace,
        ref_dir=None,
        backend=backend,
        agent_backend="deepagents",
        cli_provider=None,
        run_id="run-123",
    )
    values.update(overrides)
    values["log_dir"].mkdir(exist_ok=True)  # pyright: ignore[reportOptionalMemberAccess]  # tracked: #297
    return RunEnvironmentRequest(**values)  # pyright: ignore[reportArgumentType]  # tracked: #297


def test_cli_compatibility_flags_keep_options_scoped_to_selected_environment():  # noqa: ANN201  # tracked: #288
    assert make_run_environment_spec().options == {}
    assert make_run_environment_spec(use_docker=True, docker_image="editor").options == {
        "image": "editor"
    }

    remote = make_run_environment_spec(
        use_modal=True,
        docker_image="editor",
        modal_gpu="accelerator",
        modal_model_volume="weights",
        modal_app="candidate",
    )
    assert remote.name == "modal"
    assert remote.options == {
        "image": "editor",
        "gpu": "accelerator",
        "model_volume": "weights",
        "app": "candidate",
    }


def _modal_runtime_document(tmp_path: Path) -> str:
    return (tmp_path / "logs" / "runtime-environment.md").read_text()


def test_local_environment_opens_local_sandbox_with_host_paths(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("local"))

    session = env.open(
        _request(
            tmp_path,
            backend,
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
        )
    )

    assert backend.calls[0][0] is SandboxKind.LOCAL
    assert session.sandbox is backend.sandbox
    assert session.view.paths.accuracy_command == "uv run python accuracy_checker/checker.py"
    assert session.view.paths.benchmark_command == "uv run python benchmark/benchmark.py"
    assert session.view.isolated is False
    backend.sandbox.start.assert_not_called()


def test_local_environment_materializes_effective_objective_outside_workspace(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("local"))
    effective = "Optimize the service.\n\n## Operator constraints\n\n- BF16 only\n"

    session = env.open(_request(tmp_path, backend, objective=effective))

    objective_path = Path(session.view.paths.objective)
    assert objective_path == tmp_path / "logs" / "effective-objective.md"
    assert objective_path.read_text() == effective
    assert not objective_path.is_relative_to(tmp_path / "workspace")


def test_docker_environment_opens_one_started_sandbox_with_agent_paths(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))

    session = env.open(
        _request(
            tmp_path,
            backend,
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py",
        )
    )

    assert backend.calls[0][0] is SandboxKind.DOCKER
    assert session.view.isolated is True
    assert session.view.cli_sandboxed is True
    assert session.view.profile_execution == "local"
    assert session.view.paths.accuracy_command == "uv run python accuracy_checker/checker.py"
    assert session.view.paths.benchmark_command == "uv run python benchmark/benchmark.py"
    assert backend.calls[0][1]["extra_env"]["UV_CACHE_DIR"] == "/workspace/.cache/uv"
    backend.sandbox.start.assert_called_once()

    session.close()
    backend.sandbox.stop.assert_called_once()


def test_isolated_environment_mounts_and_translates_evaluator_package(tmp_path: Path) -> None:
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))
    package = resolve_evaluator_package(
        EvaluatorPackageRequirement(name="vibesys-evaluator-queue", version="0.1.0")
    )
    command = shlex.join(
        package.command(
            "vibesys-queue",
            "check",
            "--workspace",
            "${PROJECT_ROOT}",
            "--nested-json",
            f'["go","-C","{package.root}"]',
        )
    )

    session = env.open(
        _request(
            tmp_path,
            backend,
            accuracy_command=command,
            benchmark_command=command,
            evaluator_package_root=package.root,
        )
    )

    translated = session.view.paths.accuracy_command
    assert translated is not None
    assert str(package.root) not in translated
    assert "${PROJECT_ROOT}" not in translated
    assert "/opt/vibesys-evaluator-package" in translated
    assert "/workspace" in translated
    assert (
        str(package.root),
        "/opt/vibesys-evaluator-package",
        True,
    ) in backend.calls[0][1]["bind_mounts"]
    init_commands = backend.calls[0][1]["extra_init_commands"]
    assert any("go1.23.12" in item for item in init_commands)
    assert any("rustup.rs" in item for item in init_commands)
    assert any("command -v cargo" in item for item in init_commands)


def test_environment_quotes_project_root_after_token_expansion(tmp_path: Path) -> None:
    backend = FakeBackend()
    workspace = tmp_path / "candidate's; touch injected"
    workspace.mkdir()
    env = build_run_environment(RunEnvironmentSpec("local"))

    request = _request(
        tmp_path,
        backend,
        workspace=workspace,
        accuracy_command="python checker.py --workspace '${PROJECT_ROOT}'",
        benchmark_command="true",
    )
    session = env.open(request)

    command = session.view.paths.accuracy_command
    assert command is not None
    assert shlex.split(command) == [
        "python",
        "checker.py",
        "--workspace",
        str(request.workspace),
    ]


def test_environment_quotes_hotel_nested_shell_paths(tmp_path: Path) -> None:
    backend = FakeBackend()
    workspace = tmp_path / "candidate's; touch injected"
    workspace.mkdir()
    project = Path("examples/microservices/repositories/deathstarbench").resolve()
    layout = ProjectLayout.open(project)
    bundle = load_project_task(layout, layout.select_task("hotel-reservation"))
    env = build_run_environment(RunEnvironmentSpec("local"))

    session = env.open(
        _request(
            tmp_path,
            backend,
            workspace=workspace,
            accuracy_command=bundle.accuracy_command_display,
            benchmark_command=bundle.benchmark_command_display,
            evaluator_package_root=bundle.evaluator_package_root,
        )
    )

    command = session.view.paths.benchmark_command
    assert command is not None
    outer = shlex.split(command)
    nested = json.loads(outer[outer.index("--run-command-json") + 1])
    assert nested[5] == str(workspace / "hotelReservation" / "docker-compose.yml")
    assert "${PROJECT_ROOT}" not in json.dumps(nested)


@pytest.mark.parametrize(
    "nested",
    [
        '["sh","-c","printf \\"%s\\" \\"${PROJECT_ROOT}\\""]',
        '["bash","-ec","printf %s ${PROJECT_ROOT}"]',
        '["bash","-o","pipefail","-c","printf %s ${PROJECT_ROOT}"]',
        '["/usr/bin/bash","-c","printf %s ${PROJECT_ROOT}"]',
        '["env","sh","-c","printf %s ${PROJECT_ROOT}"]',
        '["/usr/bin/env","-i","MODE=test","bash","-ec","printf %s ${PROJECT_ROOT}"]',
        '["env","-S","sh -c \'printf %s ${PROJECT_ROOT}\'"]',
    ],
)
def test_environment_rejects_semantic_tokens_in_nested_shell_source(
    tmp_path: Path,
    nested: str,
) -> None:
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("local"))

    with pytest.raises(ValueError, match="positional arguments"):
        env.open(
            _request(
                tmp_path,
                backend,
                accuracy_command=shlex.join(["checker", "--run-command-json", nested]),
                benchmark_command="true",
            )
        )


@pytest.mark.parametrize(
    "command",
    [
        ["sh", "-c", "printf %s ${PROJECT_ROOT}"],
        ["python", "-c", "print('${PROJECT_ROOT}')"],
        ["node", "--eval", "console.log('${PROJECT_ROOT}')"],
    ],
)
def test_environment_rejects_semantic_tokens_in_top_level_executable_source(
    tmp_path: Path,
    command: list[str],
) -> None:
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("local"))

    with pytest.raises(ValueError, match="positional arguments"):
        env.open(
            _request(
                tmp_path,
                backend,
                accuracy_command=shlex.join(command),
                benchmark_command="true",
            )
        )


def test_microservice_package_does_not_install_rust(tmp_path: Path) -> None:
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))
    package = resolve_evaluator_package(
        EvaluatorPackageRequirement(
            name="vibesys-evaluator-microservice",
            version="0.1.0",
        )
    )

    env.open(
        _request(
            tmp_path,
            backend,
            accuracy_command="true",
            benchmark_command="true",
            evaluator_package_root=package.root,
        )
    )

    init_commands = backend.calls[0][1]["extra_init_commands"]
    assert any("go1.23.12" in item for item in init_commands)
    assert not any("rustup.rs" in item for item in init_commands)


def test_docker_environment_mounts_effective_objective_read_only(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))
    effective = "Optimize.\n\n## Operator constraints\n\n- exact BF16\n"

    session = env.open(_request(tmp_path, backend, objective=effective))

    host_path = tmp_path / "logs" / "effective-objective.md"
    assert host_path.read_text() == effective
    assert (
        str(host_path),
        "/opt/vibesys-runtime/objective.md",
        True,
    ) in backend.calls[0][1]["bind_mounts"]
    assert "/opt/vibesys-runtime" in backend.calls[0][1]["passthrough_paths"]
    assert session.view.paths.objective == "/opt/vibesys-runtime/objective.md"


@pytest.mark.parametrize("environment_name", ["docker", "modal"])
def test_isolated_environment_enforces_project_path_policy(tmp_path, environment_name):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec(environment_name))
    project = tmp_path / "workspace"
    (project / ".git").mkdir(parents=True)
    (project / ".state" / "local").mkdir(parents=True)
    (project / ".state" / "project.json").write_text("{}\n")
    (project / "vibesys.input.toml").write_text("version = 1\n")
    (project / "agent.toml").write_text("[model]\nname = 'private'\n")
    policy = ProjectPathPolicy(
        read_only_paths=(".git", ".state", "vibesys.input.toml"),
        hidden_paths=(".state/local", "agent.toml"),
    )

    env.open(
        _request(
            tmp_path,
            backend,
            agent_backend="cli",
            cli_provider="codex",
            project_path_policy=policy,
        )
    )

    mounts = backend.calls[0][1]["bind_mounts"]
    assert (str(project / ".git"), "/workspace/.git", True) in mounts
    assert (str(project / ".state"), "/workspace/.state", True) in mounts
    assert (
        str(project / "vibesys.input.toml"),
        "/workspace/vibesys.input.toml",
        True,
    ) in mounts
    hidden_mounts = {
        container: Path(host)
        for host, container, read_only in mounts
        if read_only and container in {"/workspace/.state/local", "/workspace/agent.toml"}
    }
    assert hidden_mounts["/workspace/.state/local"].is_dir()
    assert hidden_mounts["/workspace/agent.toml"].is_file()
    assert hidden_mounts["/workspace/.state/local"].is_relative_to(tmp_path / "logs")
    assert hidden_mounts["/workspace/agent.toml"].is_relative_to(tmp_path / "logs")


def test_docker_environment_copies_cli_auth_from_readonly_staging(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))
    auth_file = tmp_path / "synthetic-codex-home" / "auth.json"
    auth_file.parent.mkdir()
    auth_file.write_text('{"synthetic": true}\n')
    monkeypatch.setitem(
        cli_docker.DOCKER_AUTH_PATHS,
        "codex",
        [DockerAuthPath(auth_file, "/root/.codex/auth.json")],
    )

    env.open(_request(tmp_path, backend, agent_backend="cli", cli_provider="codex"))

    kwargs = backend.calls[0][1]
    assert (str(auth_file), "/opt/vibesys-auth/0", True) in kwargs["bind_mounts"]
    assert kwargs["extra_init_commands"][0] == (
        "mkdir -p /root/.codex && cp -a /opt/vibesys-auth/0 /root/.codex/auth.json"
    )


def test_docker_environment_exposes_framework_git_history_read_only(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))
    history = tmp_path / "experiment-history"
    history.mkdir()

    session = env.open(
        _request(
            tmp_path,
            backend,
            agent_backend="cli",
            cli_provider="codex",
            git_history_root=history,
        )
    )

    kwargs = backend.calls[0][1]
    assert (str(history), "/opt/vibesys-history", True) in kwargs["bind_mounts"]
    assert "/opt/vibesys-history" in kwargs["passthrough_paths"]
    assert kwargs["extra_env"]["VIBESYS_GIT_HISTORY"] == "/opt/vibesys-history"
    assert "/opt/vibesys-history" in session.view.prompt_notes
    assert "hashes without recoverable source are insufficient" in session.view.prompt_notes


def test_docker_environment_uses_environment_bind_mounts(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    env.open(
        _request(
            tmp_path,
            backend,
            environment_bind_mounts=(EnvironmentBindMount(model_dir, "/model", True),),  # noqa: FBT003  # tracked: #288
        )
    )

    kwargs = backend.calls[0][1]
    assert (str(model_dir), "/model", True) in kwargs["bind_mounts"]
    assert "/model" in kwargs["passthrough_paths"]


def test_docker_environment_mounts_selected_profiler_support(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))
    support = tmp_path / "custom-profiler"
    support.mkdir()

    session = env.open(
        _request(
            tmp_path,
            backend,
            profiler_support_path=str(support),
            profiler_support_name="fixture_profiler",
        )
    )

    kwargs = backend.calls[0][1]
    assert (str(support), "/workspace/fixture_profiler", True) in kwargs["bind_mounts"]
    assert session.view.paths.profiler_support == "fixture_profiler"


def test_docker_environment_does_not_infer_model_mount_from_reference_dir(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))
    ref_dir = tmp_path / "reference"
    (ref_dir / "model").mkdir(parents=True)

    env.open(_request(tmp_path, backend, ref_dir=ref_dir))

    bind_mounts = backend.calls[0][1]["bind_mounts"]
    assert all(container_path != "/model" for _, container_path, _ in bind_mounts)


def test_environment_session_context_manager_closes(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))

    with env.open(_request(tmp_path, backend)) as session:
        assert session.sandbox is backend.sandbox
        backend.sandbox.stop.assert_not_called()

    backend.sandbox.stop.assert_called_once()
    session.close()
    backend.sandbox.stop.assert_called_once()


def test_modal_environment_uses_local_docker_for_editing(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """Post-refactor (April 2026): Modal mode runs the agent in a local
    Docker container; only GPU-bound work the implementer dispatches via
    `modal run` actually touches Modal."""
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))

    session = env.open(_request(tmp_path, backend, agent_backend="cli", cli_provider="codex"))

    # The sandbox is local Docker, not a Modal Sandbox.
    assert backend.calls[0][0] is SandboxKind.DOCKER
    assert backend.calls[0][1]["attach_accelerator"] is False
    assert session.view.cli_sandboxed is True
    assert session.view.profile_execution == "remote"
    assert session.view.deployment_namespace is not None
    assert session.view.supports_parallel_candidate_evaluation is True
    assert session.view.deployment_release_env_var == "VIBESYS_RELEASE_MODAL_DEPLOYMENT"
    backend.sandbox.start.assert_called_once()


def test_modal_environment_owns_candidate_runtime_naming(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))
    session = env.open(_request(tmp_path, backend, agent_backend="cli", cli_provider="codex"))

    runtime = env.candidate_runtime(session.view, generation=12, child_idx=7)

    assert runtime.deployment_name is not None
    assert runtime.deployment_name.endswith("-g12c7")
    assert len(runtime.deployment_name) <= 63
    assert session.view.deployment_namespace in runtime.prompt_notes  # pyright: ignore[reportOperatorIssue]  # tracked: #297
    assert "Candidate-specific namespace override" in runtime.prompt_notes
    assert runtime.deployment_name in runtime.prompt_notes


def test_modal_environment_wraps_service_evaluators_with_remote_dispatch(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))

    session = env.open(
        _request(
            tmp_path,
            backend,
            agent_backend="cli",
            cli_provider="codex",
            accuracy_command="uv run python accuracy_checker/checker.py",
            benchmark_command="uv run python benchmark/benchmark.py --concurrency 16",
        )
    )

    helper = "/opt/vibesys-modal-evaluator.py"
    prefix = f"python {helper} --readiness-timeout-seconds 1200 --"
    assert session.view.paths.accuracy_command == (
        f"{prefix} uv run python accuracy_checker/checker.py"
    )
    assert session.view.paths.benchmark_command == (
        f"{prefix} uv run python benchmark/benchmark.py --concurrency 16"
    )
    assert session.view.framework_setup_timeout_seconds == 1200
    assert any(
        container_path == helper and read_only
        for _, container_path, read_only in backend.calls[0][1]["bind_mounts"]
    )


def test_modal_environment_installs_modal_sdk_in_docker(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """The local Docker container needs the Modal Python SDK installed so
    the implementer-authored `modal run` calls work."""
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))

    env.open(_request(tmp_path, backend, agent_backend="cli", cli_provider="codex"))

    commands = backend.calls[0][1]["extra_init_commands"]
    assert any("pip install" in c and "modal" in c for c in commands), (
        f"expected `pip install modal` in init commands, got: {commands}"
    )
    assert backend.calls[0][1]["extra_env"]["UV_CACHE_DIR"] == "/workspace/.cache/uv"


def test_modal_environment_prompt_references_runtime_document(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """Prompts name the runtime manual instead of embedding it in every role."""
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))

    session = env.open(_request(tmp_path, backend, agent_backend="cli", cli_provider="codex"))

    notes = session.view.prompt_notes
    runtime = _modal_runtime_document(tmp_path)

    assert notes == (
        "Runtime instructions are at `/opt/vibesys-runtime/environment.md`. Read that "
        "file before executing, deploying, benchmarking, or profiling; it contains "
        "the authoritative environment and lifecycle rules."
    )
    assert "modal run" not in notes
    assert "modal run" in runtime
    assert "@app.cls" in runtime or "@app.function" in runtime
    assert "GPU" in runtime
    assert any(
        container_path == "/opt/vibesys-runtime/environment.md" and read_only
        for _, container_path, read_only in backend.calls[0][1]["bind_mounts"]
    )
    assert "/opt/vibesys-runtime" in backend.calls[0][1]["passthrough_paths"]
    # Tell the agent where to look up volume names rather than baking them in.
    assert "meta.json" in runtime
    # No hardcoded model IDs or vibesys-internal volume names should leak
    # into the runtime-notes block.
    forbidden = (
        "yuhuili",
        "Llama-3",
        "vibesys-model-meta-llama",
        "vibesys-model-yuhuili",
    )
    for token in forbidden:
        assert token not in runtime, f"runtime manual leaks task-specific token {token!r}"
    prior_solution_terms = (
        "EAGLE3",
        "speculative decoding",
        "CUDA graphs",
        "FlashAttention",
        "continuous batching",
        "paged attention",
    )
    for term in prior_solution_terms:
        assert term.casefold() not in runtime.casefold()


def test_modal_environment_mounts_effective_objective_read_only(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))
    effective = "Optimize.\n\n## Operator constraints\n\n- no quantization\n"

    session = env.open(
        _request(
            tmp_path,
            backend,
            agent_backend="cli",
            cli_provider="codex",
            objective=effective,
        )
    )

    host_path = tmp_path / "logs" / "effective-objective.md"
    assert host_path.read_text() == effective
    assert (
        str(host_path),
        "/opt/vibesys-runtime/objective.md",
        True,
    ) in backend.calls[0][1]["bind_mounts"]
    assert session.view.paths.objective == "/opt/vibesys-runtime/objective.md"


def test_modal_environment_prompt_notes_require_remote_runtime_fingerprint(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))

    env.open(_request(tmp_path, backend, agent_backend="cli", cli_provider="codex"))
    notes = _modal_runtime_document(tmp_path)

    assert "authoritative runtime" in notes
    assert "runtime fingerprint" in notes
    assert "must not be used to infer remote compatibility" in notes
    assert "same Modal image and hardware" in notes


def test_modal_environment_requires_exact_default_h100_identity(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))

    env.open(_request(tmp_path, backend, agent_backend="cli", cli_provider="codex"))
    notes = _modal_runtime_document(tmp_path)

    assert "gpu='H100!'" in notes
    assert "Accelerator identity is an experimental contract" in notes
    assert "Modal may upgrade bare `H100` requests to H200" in notes
    assert "fail closed" in notes


def test_modal_environment_documents_history_and_exact_measurement_source(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))
    history = tmp_path / "experiment-history"
    history.mkdir()

    env.open(
        _request(
            tmp_path,
            backend,
            agent_backend="cli",
            cli_provider="codex",
            git_history_root=history,
        )
    )
    kwargs = backend.calls[0][1]
    notes = _modal_runtime_document(tmp_path)

    assert (str(history), "/opt/vibesys-history", True) in kwargs["bind_mounts"]
    assert kwargs["extra_env"]["VIBESYS_GIT_HISTORY"] == "/opt/vibesys-history"
    assert "git -c safe.directory=/opt/vibesys-history" in notes
    assert "ls-tree -r --name-only <commit>" in notes
    assert "Do not run `git checkout`" in notes
    assert "preserving Git HEAD, roadmap/progress/Pareto" in notes
    assert "manifest containing only per-file hashes is not sufficient" in notes
    assert "Create this provenance artifact before launch" in notes


def test_modal_environment_uses_explicit_run_id_for_namespace(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """Project location does not participate in remote resource identity."""
    backend_a = FakeBackend()
    backend_b = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))

    ws_a = tmp_path / "projects" / "queue-a"
    ws_a.mkdir(parents=True)
    ws_b = tmp_path / "projects" / "queue-b"
    ws_b.mkdir(parents=True)

    log_a = tmp_path / "logsA"
    log_a.mkdir(exist_ok=True)
    log_b = tmp_path / "logsB"
    log_b.mkdir(exist_ok=True)

    req_a = RunEnvironmentRequest(
        log_dir=log_a,
        workspace=ws_a,
        ref_dir=None,
        backend=backend_a,  # pyright: ignore[reportArgumentType]  # tracked: #297
        agent_backend="cli",
        cli_provider="codex",
        run_id="20260429-100000-runa",
    )
    req_b = RunEnvironmentRequest(
        log_dir=log_b,
        workspace=ws_b,
        ref_dir=None,
        backend=backend_b,  # pyright: ignore[reportArgumentType]  # tracked: #297
        agent_backend="cli",
        cli_provider="codex",
        run_id="20260429-100100-runb",
    )
    env.open(req_a)
    env.open(req_b)
    notes_a = (log_a / "runtime-environment.md").read_text()
    notes_b = (log_b / "runtime-environment.md").read_text()

    assert "vibesys-20260429-100000-runa" in notes_a
    assert "vibesys-20260429-100100-runb" in notes_b
    assert "vibesys-20260429-100000-runa" not in notes_b
    assert "vibesys-20260429-100100-runb" not in notes_a


def test_modal_environment_runtime_notes_describe_profile_contract(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """The runtime notes must spell out the modal_profile / profile_remote
    contract; without it the profiler agent has no Modal entrypoint to
    invoke and falls back to local synthetic-weight profiling."""
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))

    env.open(_request(tmp_path, backend, agent_backend="cli", cli_provider="codex"))
    notes = _modal_runtime_document(tmp_path)

    assert "modal_profile" in notes
    assert "profile_remote" in notes
    assert "@app.local_entrypoint()" in notes
    assert "torch.profiler" in notes
    # Schema reference for the analyzer-compatible JSON shape.
    assert "analyze_torch_profile.py" in notes
    assert "_summarize_prof" in notes
    assert "total_cuda_time_us" not in notes
    assert "from torch.autograd import DeviceType" not in notes


def test_modal_environment_prompt_notes_reuse_workspace_uv_cache(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))

    env.open(_request(tmp_path, backend, agent_backend="cli", cli_provider="codex"))
    notes = _modal_runtime_document(tmp_path)

    assert "UV_CACHE_DIR=/workspace/.cache/uv" in notes
    assert "persist outside Git checkpoints" in notes
    assert "do not delete or recreate `.venv`" in notes
    assert ".venv/bin/python -m ..." in notes
    assert "excluding `.venv` and `.cache`" in notes


def test_modal_environment_with_deepagents_uses_docker_too(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """The deepagents path also runs locally in Docker now — Modal is a
    dispatch target, not a runtime for the agent."""
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))

    env.open(_request(tmp_path, backend, agent_backend="deepagents"))

    assert backend.calls[0][0] is SandboxKind.DOCKER


def test_unknown_environment_name_raises():  # noqa: ANN201  # tracked: #288
    with pytest.raises(ValueError, match="unknown run environment"):
        build_run_environment(RunEnvironmentSpec("wat"))


def test_docker_remove_workspace_child_quotes_path(tmp_path, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("docker"))
    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001  # tracked: #288
        calls.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = b""
        return result

    monkeypatch.setattr("vibesys.sandbox.run_environment.subprocess.run", fake_run)

    ok = env.remove_workspace_child(
        tmp_path,
        "semi;touch hacked",
        backend=backend,  # pyright: ignore[reportArgumentType]  # tracked: #297
    )

    assert ok is True
    shell_command = calls[0][-1]
    assert "rm -rf -- " in shell_command
    assert "'/workspace/semi;touch hacked'" in shell_command


def test_modal_teardown_deployment_stops_app_via_cli(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    import sys as _sys  # noqa: PLC0415  # tracked: #288

    env = build_run_environment(RunEnvironmentSpec("modal"))
    calls = []
    logs = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001  # tracked: #288
        calls.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    monkeypatch.setattr("vibesys.sandbox.run_environment.subprocess.run", fake_run)

    env.teardown_deployment("vibesys-run-g1c2", log=logs.append)

    assert calls == [[_sys.executable, "-m", "modal", "app", "stop", "vibesys-run-g1c2", "--yes"]]
    assert any("stopped candidate app vibesys-run-g1c2" in line for line in logs)


def test_modal_teardown_deployment_is_best_effort_on_nonzero(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    env = build_run_environment(RunEnvironmentSpec("modal"))
    logs = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001  # tracked: #288
        result = MagicMock()
        result.returncode = 1
        result.stderr = "boom"
        return result

    monkeypatch.setattr("vibesys.sandbox.run_environment.subprocess.run", fake_run)

    # Must not raise.
    env.teardown_deployment("vibesys-run-g1c2", log=logs.append)
    assert any("failed" in line for line in logs)


def test_modal_teardown_deployment_is_best_effort_on_exception(monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    env = build_run_environment(RunEnvironmentSpec("modal"))
    logs = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001  # tracked: #288
        raise TimeoutError("stuck")

    monkeypatch.setattr("vibesys.sandbox.run_environment.subprocess.run", fake_run)

    env.teardown_deployment("vibesys-run-g1c2", log=logs.append)
    assert any("raised" in line for line in logs)


@pytest.mark.parametrize("name", ["local", "docker"])
def test_non_modal_teardown_deployment_is_noop(name, monkeypatch):  # noqa: ANN001, ANN201  # tracked: #288
    env = build_run_environment(RunEnvironmentSpec(name))

    def fail_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001  # tracked: #288
        raise AssertionError("subprocess.run should not be called for non-Modal envs")  # noqa: TRY003  # tracked: #288

    monkeypatch.setattr("vibesys.sandbox.run_environment.subprocess.run", fail_run)

    # No deployment to stop — must be a silent no-op.
    env.teardown_deployment("vibesys-run-g1c2", log=lambda _: None)


def test_modal_environment_prompt_notes_cover_seeded_checkouts(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    """Seeded starting-point checkouts live only in the editor container, so
    the runtime notes must tell the agent to bake them into the Modal image;
    unseeded runs must not mention checkouts at all."""
    from vibesys.input_manifest import WorkspaceSource  # noqa: PLC0415  # tracked: #288

    backend = FakeBackend()
    env = build_run_environment(RunEnvironmentSpec("modal"))
    source = WorkspaceSource(
        name="vllm",
        repo="https://github.com/vllm-project/vllm",
        commit="d7de043d55d1dd629554467e23874097e1c48993",
        dest="vllm",
    )

    seeded = env.open(
        _request(
            tmp_path,
            backend,
            agent_backend="cli",
            cli_provider="codex",
            workspace_sources=(source,),
        )
    )
    assert "seeded starting-point" not in seeded.view.prompt_notes.lower()
    notes = _modal_runtime_document(tmp_path)
    assert "`vllm/`" in notes
    assert "add_local_dir" in notes
    assert "copy=True" in notes

    unseeded_dir = tmp_path / "unseeded"
    unseeded_dir.mkdir()
    env.open(_request(unseeded_dir, backend, agent_backend="cli", cli_provider="codex"))
    unseeded_notes = _modal_runtime_document(unseeded_dir)
    assert "add_local_dir('vllm'" not in unseeded_notes
    assert "seeded starting-point" not in unseeded_notes.lower()
