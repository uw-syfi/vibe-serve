"""Backend-agnostic agent runner package.

The ``build_agent_runner`` function is added at the bottom once the concrete
runner classes are imported, so the public API of this package is::

    from vibesys.agents import AgentRunner, RoundProgress, build_agent_runner
"""

from __future__ import annotations

from collections.abc import Iterable  # noqa: TC003  # tracked: #288
from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import TYPE_CHECKING, Any, TextIO

from vibesys.config import Config  # noqa: TC001  # tracked: #288
from vibesys.constants import DEFAULT_AGENT_BACKEND, ComputeBackend
from vibesys.features import FeatureFlag, is_feature_enabled

from .base import AgentRunner
from .progress import AgentProgress, CandidateProgress, RoundProgress

# ``CliAgentRunner`` and ``DeepAgentsRunner`` are imported lazily to break a
# circular import: ``vibesys.agent_runner`` imports
# ``vibesys.agents.callbacks`` (which triggers this ``__init__``), and both
# concrete runners import back from ``vibesys.agent_runner``. Importing
# them eagerly here turned the cycle into an ImportError on the first entry
# point that hits ``agent_runner`` first (e.g. the ``vibesys`` script).
# The ``TYPE_CHECKING`` imports let pyright resolve the lazily provided
# names statically without triggering the runtime cycle.
if TYPE_CHECKING:
    from vs_sandbox import HostResource

    from .cli_runner import CliAgentRunner
    from .deepagents_runner import DeepAgentsRunner

__all__ = [
    "AgentProgress",
    "AgentRunner",
    "CandidateProgress",
    "CliAgentRunner",
    "DeepAgentsRunner",
    "RoundProgress",
    "build_agent_runner",
]


def __getattr__(name: str) -> Any:  # noqa: ANN401  # tracked: #288
    if name == "CliAgentRunner":
        from .cli_runner import CliAgentRunner  # noqa: PLC0415  # tracked: #288

        return CliAgentRunner
    if name == "DeepAgentsRunner":
        from .deepagents_runner import DeepAgentsRunner  # noqa: PLC0415  # tracked: #288

        return DeepAgentsRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")  # noqa: TRY003  # tracked: #288


def build_agent_runner(  # noqa: D417, PLR0913  # tracked: #288
    config: Config,
    *,
    agent_backend: str | None,
    cli_provider: str | None,
    backends: dict[str, Any] | None,
    skills: list[str],
    skill_source_dirs: list[Path],
    compute_backend: ComputeBackend | None = None,
    model: Any,  # noqa: ANN401  # tracked: #288
    model_name: str,
    run_log_file: TextIO | None,
    use_docker: bool,
    log_dir: Path | None = None,
    host_resources: Iterable[HostResource] = (),
) -> AgentRunner:
    """Construct the right :class:`AgentRunner` for the requested backend.

    Args:
        config: Parsed :class:`~vibesys.config.Config`; the ``[agent]``
            section drives backend/provider/model/timeout selection.
        agent_backend: CLI override; if set, takes precedence over
            ``config.agent.backend``. Falls back to
            :data:`vibesys.constants.DEFAULT_AGENT_BACKEND` (``"cli"``).
        cli_provider: CLI override for ``config.agent.cli_provider``.
        backends: Mapping ``{"implementer": BaseSandbox, "judge": ..., "perf_eval": ...}``.
            Required for the deepagents path; ignored for the cli path.
        skills: Skill directory names already materialized in the workspace,
            for the deepagents backend (passed through to ``create_deep_agent``).
        skill_source_dirs: Absolute source paths of skill directories — the
            cli runner copies these into the workspace's ``.claude/skills/``
            on each invoke.
        model: Result of :func:`vibesys.llm_client.build_model` — only
            used by the deepagents path. The cli path uses ``model_name``.
        model_name: Bare model id (e.g. ``"claude-sonnet-4-6"``) — used both
            for the cli runner and for the deepagents ``AgentLogger`` prefix.
        run_log_file: Open text-mode file handle for the loop's main log.
        use_docker: Whether the loop is running with ``--docker``. The cli
            runner refuses Docker mode (CLI tools have their own sandboxing).
        log_dir: Optional directory where the cli runner appends per-invoke
            usage/cost records (``<log_dir>/usage.jsonl``). Ignored by the
            deepagents backend, which already shows live token counts in
            its callback prefix and would need a separate cleanup for JSONL
            persistence.
        host_resources: Provider-independent host resource declarations for
            local CLI agents. Ignored by container and non-CLI backends, whose
            run-environment importers own resource exposure.

    Raises:
        SystemExit: If the requested backend is unknown, the cli backend was
            combined with ``--docker``, or the cli backend was selected
            without a provider.
        OmnigentUnavailableError: If the ``omnigent_agent_backend`` feature
            flag is enabled but the requested provider has no Omnigent
            executor, or the flag was combined with ``--docker``. Never raised
            with the flag off, which is the default.
    """
    agent_cfg = config.agent
    backend = agent_backend or agent_cfg.backend or DEFAULT_AGENT_BACKEND

    if backend == "deepagents":
        if backends is None:
            raise SystemExit(  # noqa: TRY003  # tracked: #288
                "internal error: build_agent_runner called with backend='deepagents' "
                "but no backends dict was provided"
            )
        from .deepagents_runner import DeepAgentsRunner  # noqa: PLC0415  # tracked: #288

        return DeepAgentsRunner(
            model=model,
            backends=backends,
            skills=skills,
            model_name=model_name,
            run_log_file=run_log_file,
        )

    if backend == "stub":
        from .stub_runner import StubAgentRunner  # noqa: PLC0415  # tracked: #288

        return StubAgentRunner()

    if backend == "cli":
        provider = cli_provider or agent_cfg.cli_provider or "codex"
        timeout = agent_cfg.cli_timeout

        if is_feature_enabled(FeatureFlag.OMNIGENT_AGENT_BACKEND, config):
            # Opt-in alternative to agentshim. Validated and constructed here
            # so an unsupported provider or a missing optional dependency
            # fails before the loop starts. With the flag off (the default)
            # this branch is never entered and nothing imports omnigent.
            from .omnigent.runner import (  # noqa: PLC0415  # tracked: #288
                OmnigentAgentRunner,
                OmnigentUnavailableError,
            )

            if use_docker:
                raise OmnigentUnavailableError(  # noqa: TRY003  # tracked: #288
                    "feature flag 'omnigent_agent_backend' is not supported with "
                    "--docker; this integration has no container-launcher "
                    "support. Drop --docker or disable the flag."
                )
            extra_resources = tuple(host_resources)
            if extra_resources:
                # These are explicit operator grants that the agentshim path
                # feeds to declare_agent_host_resources. Omnigent's sandbox
                # takes its own path vocabulary and this integration does not
                # translate them, so honouring the request is impossible and
                # dropping it silently would weaken a security boundary the
                # caller asked for.
                raise OmnigentUnavailableError(  # noqa: TRY003  # tracked: #288
                    "the Omnigent backend cannot honour the requested host "
                    f"resource grants ({[str(r.path) for r in extra_resources]}); "
                    "it confines the agent to the workspace only. Disable "
                    "'omnigent_agent_backend' to use the agentshim backend, "
                    "which declares these through vs_sandbox."
                )
            return OmnigentAgentRunner(
                provider=provider,
                model=model_name,
                skills=skill_source_dirs,
                model_name=model_name or provider,
                timeout=timeout,
                run_log_file=run_log_file,
                log_dir=log_dir,
            )

        docker_sandboxes = None
        if use_docker:
            # The Docker CLI path reuses the DOCKER_PROVIDER_ENV registry for
            # per-provider install + env requirements (node/npm, codex binary,
            # PYTHONPATH, etc).
            from .cli_docker import DOCKER_PROVIDER_ENV  # noqa: PLC0415  # tracked: #288

            if provider not in DOCKER_PROVIDER_ENV:
                raise SystemExit(  # noqa: TRY003  # tracked: #288
                    f"--cli-provider {provider!r} is not yet supported with --docker; "
                    f"supported: {sorted(DOCKER_PROVIDER_ENV)}"
                )
            docker_sandboxes = backends
        # The CLI tool runs [model].name, same as the deepagents backend —
        # it's the single source of truth for the model. model.name is a
        # required field (and always validated by build_model), so it is
        # always a non-empty string here.
        from .cli_runner import CliAgentRunner  # noqa: PLC0415  # tracked: #288

        return CliAgentRunner(
            provider=provider,
            model=model_name,
            skills=skill_source_dirs,
            compute_backend=compute_backend,
            model_name=model_name or provider,
            timeout=timeout,
            run_log_file=run_log_file,
            docker_sandboxes=docker_sandboxes,
            host_resources=host_resources,
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
        )

    raise SystemExit(f"unknown agent backend: {backend!r}")  # noqa: TRY003  # tracked: #288
