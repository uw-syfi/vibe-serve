"""Microservices domain definition."""

from __future__ import annotations

from vibesys.domains.base import DomainDefinition, DomainName
from vibesys.domains.environment import NoopEnvironmentHooks
from vibesys.prompts import PROMPTS_DIR

DEFINITION = DomainDefinition(
    name=DomainName.MICROSERVICES,
    prompt_dir=PROMPTS_DIR / "domains" / "microservices",
    environment_hooks=NoopEnvironmentHooks(),
)
