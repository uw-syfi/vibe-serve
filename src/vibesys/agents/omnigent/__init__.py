"""Opt-in Omnigent agent backend.

Selected only when the ``omnigent_agent_backend`` feature flag is enabled;
see :doc:`docs/omnigent-evaluation` for why this is opt-in rather than the
default. Nothing in this package is imported on the agentshim path.

``providers`` is import-safe everywhere (it holds only data). ``runner``
imports ``omnigent`` lazily, inside the methods that need it, so importing
this package never requires the optional dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.agents.omnigent.providers import (
    OMNIGENT_PROVIDER_EXECUTORS,
    OmnigentExecutorSpec,
    supported_providers,
)

if TYPE_CHECKING:
    # Statically visible without triggering the runtime import below.
    from vibesys.agents.omnigent.runner import (
        OmnigentAgentRunner,
        OmnigentUnavailableError,
    )

__all__ = [
    "OMNIGENT_PROVIDER_EXECUTORS",
    "OmnigentAgentRunner",
    "OmnigentExecutorSpec",
    "OmnigentUnavailableError",
    "supported_providers",
]


def __getattr__(name: str) -> object:
    # Deferred so that `import vibesys.agents.omnigent` stays cheap and does
    # not pull in langchain/pydantic wiring for callers that only need the
    # provider table (e.g. build_agent_runner's validation path).
    if name in {"OmnigentAgentRunner", "OmnigentUnavailableError"}:
        from vibesys.agents.omnigent import runner

        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
