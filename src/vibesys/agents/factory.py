"""Application composition for agent clients and execution drivers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vibesys.agents.client import AgentClient, AgentDiagnosticLog
from vibesys.constants import DEFAULT_AGENT_BACKEND

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path
    from typing import TextIO

    from vibesys.config import Config
    from vibesys.constants import ComputeBackend
    from vs_sandbox import HostResource, ProjectPathPolicy

DEFAULT_AGENT_DRIVER = "agentshim"


def resolve_agent_driver(config: Config) -> str:
    """Resolve the configured agent driver, defaulting to agentshim."""
    return config.agent.driver or DEFAULT_AGENT_DRIVER


def build_agent_client(  # noqa: C901, PLR0912, PLR0913
    config: Config,
    *,
    agent_backend: str | None,
    cli_provider: str | None,
    backends: dict[str, Any] | None,
    skills: list[str],
    skill_source_dirs: list[Path],
    compute_backend: ComputeBackend | None = None,
    model: Any,  # noqa: ANN401
    model_name: str,
    run_log_file: TextIO | None,
    use_docker: bool,
    log_dir: Path | None = None,
    host_resources: Iterable[HostResource] = (),
    project_path_policy: ProjectPathPolicy | None = None,
    require_host_sandbox: bool = False,
) -> AgentClient:
    """Build the configured application-level agent service."""
    host_resources = tuple(host_resources)
    agent_cfg = config.agent
    backend = agent_backend or agent_cfg.backend or DEFAULT_AGENT_BACKEND

    if backend != "cli" and agent_cfg.driver is not None:
        raise SystemExit(  # noqa: TRY003  # tracked: #288
            f"agent driver {agent_cfg.driver!r} is valid only with backend='cli', not {backend!r}"
        )

    if require_host_sandbox and backend not in {"cli", "stub"}:
        raise SystemExit(  # noqa: TRY003  # tracked: #288
            "local project execution requires the CLI agent backend so VibeSys can "
            "enforce nested read-only and hidden paths"
        )

    if backend == "deepagents":
        if backends is None:
            raise SystemExit(  # noqa: TRY003  # tracked: #288
                "internal error: build_agent_client called with backend='deepagents' "
                "but no backends dict was provided"
            )
        from vibesys.agents.deepagents_runner import DeepAgentsClient  # noqa: PLC0415

        return DeepAgentsClient(
            model=model,
            backends=backends,
            skills=skills,
            model_name=model_name,
            run_log_file=run_log_file,
        )

    if backend == "stub":
        from vibesys.agents.stub_runner import StubAgentClient  # noqa: PLC0415

        return StubAgentClient()

    if backend != "cli":
        raise SystemExit(f"unknown agent backend: {backend!r}")  # noqa: TRY003  # tracked: #288

    driver_name = resolve_agent_driver(config)
    provider = cli_provider or agent_cfg.cli_provider or "codex"
    timeout = agent_cfg.cli_timeout
    driver_log = AgentDiagnosticLog(run_log_file)

    if driver_name == "omnigent":
        if use_docker:
            raise SystemExit(  # noqa: TRY003  # tracked: #288
                "agent.driver='omnigent' is not supported with --docker"
            )
        from vibesys.agents.drivers.omnigent import (  # noqa: PLC0415
            OmnigentDriver,
            OmnigentDriverError,
        )
        from vibesys.agents.omnigent.providers import (  # noqa: PLC0415
            OMNIGENT_PROVIDER_EXECUTORS,
            supported_providers,
        )

        if provider not in OMNIGENT_PROVIDER_EXECUTORS:
            raise OmnigentDriverError(  # noqa: TRY003
                f"Omnigent does not support agent provider {provider!r}; "
                f"supported providers: {supported_providers()}. Select "
                "agent.driver='agentshim' for other providers."
            )
        if host_resources:
            raise OmnigentDriverError(  # noqa: TRY003
                "Omnigent cannot enforce the requested VibeSys host-resource "
                f"grants ({[str(resource.path) for resource in host_resources]}). Select "
                "agent.driver='agentshim' for this policy."
            )

        driver = OmnigentDriver()
    else:
        docker_sandboxes = None
        if use_docker:
            from vibesys.agents.cli_docker import DOCKER_PROVIDER_ENV  # noqa: PLC0415

            if provider not in DOCKER_PROVIDER_ENV:
                raise SystemExit(  # noqa: TRY003  # tracked: #288
                    f"--cli-provider {provider!r} is not yet supported with --docker; "
                    f"supported: {sorted(DOCKER_PROVIDER_ENV)}"
                )
            docker_sandboxes = backends
        from vibesys.agents.drivers.agentshim import AgentShimDriver  # noqa: PLC0415

        driver = AgentShimDriver(
            provider=provider,
            timeout=timeout,
            docker_sandboxes=docker_sandboxes,
            log=driver_log,
        )

    return AgentClient(
        driver,
        provider=provider,
        skills=skill_source_dirs,
        compute_backend=compute_backend,
        model_name=model_name or provider,
        timeout=timeout,
        run_log_file=run_log_file,
        log_dir=log_dir,
        default_reasoning_effort=config.thinking.level,
        role_models={
            role: configured
            for role, configured in {
                "orchestrator": agent_cfg.outer.model,
                "implementer": agent_cfg.inner.model,
            }.items()
            if configured is not None
        },
        role_reasoning_efforts={
            role: configured
            for role, configured in {
                "orchestrator": agent_cfg.outer.reasoning_effort,
                "implementer": agent_cfg.inner.reasoning_effort,
            }.items()
            if configured is not None
        },
        project_path_policy=project_path_policy,
        host_resources=host_resources,
        require_host_sandbox=require_host_sandbox,
        containerized=use_docker,
        driver_log=driver_log,
    )
