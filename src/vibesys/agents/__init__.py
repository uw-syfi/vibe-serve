"""Application-level agent execution contracts and composition."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import AgentRunner, ResponseFallback
from .progress import AgentProgress, CandidateProgress, RoundProgress

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path
    from typing import TextIO

    from vibesys.config import Config
    from vibesys.constants import ComputeBackend
    from vs_sandbox import HostResource, ProjectPathPolicy

    from .client import AgentClient
    from .deepagents_runner import DeepAgentsRunner

__all__ = [
    "AgentClient",
    "AgentProgress",
    "AgentRunner",
    "CandidateProgress",
    "DeepAgentsRunner",
    "ResponseFallback",
    "RoundProgress",
    "build_agent_runner",
]


def __getattr__(name: str) -> Any:  # noqa: ANN401
    """Keep implementations lazy so callback imports cannot form a cycle."""
    if name == "AgentClient":
        from .client import AgentClient  # noqa: PLC0415

        return AgentClient
    if name == "DeepAgentsRunner":
        from .deepagents_runner import DeepAgentsRunner  # noqa: PLC0415

        return DeepAgentsRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")  # noqa: TRY003


def build_agent_runner(  # noqa: PLR0913
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
) -> AgentRunner:
    """Build an agent service through the application composition module."""
    from .factory import build_agent_runner as build  # noqa: PLC0415

    return build(
        config,
        agent_backend=agent_backend,
        cli_provider=cli_provider,
        backends=backends,
        skills=skills,
        skill_source_dirs=skill_source_dirs,
        compute_backend=compute_backend,
        model=model,
        model_name=model_name,
        run_log_file=run_log_file,
        use_docker=use_docker,
        log_dir=log_dir,
        host_resources=host_resources,
        project_path_policy=project_path_policy,
        require_host_sandbox=require_host_sandbox,
    )
