from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GenerationInput:
    prompt: str | None
    messages: tuple[tuple[str, str], ...]
    enable_thinking: bool
    min_tokens: int
    max_tokens: int
    temperature: float
    top_p: float
    top_k: int
    seed: int


@dataclass(frozen=True)
class GeneratedToken:
    token_id: int
    text: str
    index: int


@dataclass(frozen=True)
class GenerationSession:
    prompt_tokens: int
    tokens: Iterator[GeneratedToken]


class ServingEngine(Protocol):
    @property
    def ready(self) -> bool: ...

    def start(self, request: GenerationInput) -> GenerationSession: ...

    def observed_configuration(self) -> dict[str, object]: ...
