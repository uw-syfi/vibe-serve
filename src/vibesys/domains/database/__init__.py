"""Database domain definition.

Hosts in-place optimization of real database / dataflow engines.
"""

from __future__ import annotations

from vibesys.domains.base import DomainDefinition, DomainName
from vibesys.domains.environment import NoopEnvironmentHooks
from vibesys.prompts import PROMPTS_DIR

DEFINITION = DomainDefinition(
    name=DomainName.DATABASE,
    prompt_dir=PROMPTS_DIR / "domains" / "database",
    environment_hooks=NoopEnvironmentHooks(),
)
