from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from .config import (
    MODEL_ID,
    MODEL_REVISION,
    MODEL_WEIGHT_DIGEST,
    TOKENIZER_REVISION,
    EngineConfig,
)
from .types import GenerationInput, GenerationSession, ServingEngine

RUNTIME_PRODUCT = "ExactMap"
RUNTIME_VERSION = "0.1.0"
PROFILE_ID = "exactmap"
PROFILE_VERSION = "exactmap.v1"


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class StreamOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_usage: bool = True


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["Qwen/Qwen3-8B"] = MODEL_ID
    prompt: str | None = None
    messages: list[Message] | None = None
    enable_thinking: bool = True
    min_tokens: int = Field(default=0, ge=0, le=8_192)
    max_tokens: int = Field(default=128, ge=1, le=8_192)
    temperature: float = Field(default=0, ge=0, le=2)
    top_p: float = Field(default=1, gt=0, le=1)
    top_k: int = Field(default=0, ge=0, le=256)
    seed: int = Field(default=0, ge=0, le=2**63 - 1)
    stream: bool = True
    stream_options: StreamOptions = Field(default_factory=StreamOptions)

    @model_validator(mode="after")
    def _validate_shape(self) -> GenerationRequest:
        if (self.prompt is None) == (self.messages is None):
            raise ValueError("exactly one of prompt or messages is required")
        if self.messages is not None and not self.messages:
            raise ValueError("messages must not be empty")
        if self.min_tokens > self.max_tokens:
            raise ValueError("min_tokens must not exceed max_tokens")
        return self

    def to_engine_input(self) -> GenerationInput:
        return GenerationInput(
            prompt=self.prompt,
            messages=tuple((item.role, item.content) for item in self.messages or ()),
            enable_thinking=self.enable_thinking,
            min_tokens=self.min_tokens,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            seed=self.seed,
        )


@dataclass
class Counters:
    requests_started: int = 0
    requests_completed: int = 0
    requests_failed: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "requestsStarted": self.requests_started,
            "requestsCompleted": self.requests_completed,
            "requestsFailed": self.requests_failed,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
        }


class RuntimeState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters = Counters()

    def started(self, prompt_tokens: int) -> None:
        with self._lock:
            self._counters.requests_started += 1
            self._counters.prompt_tokens += prompt_tokens

    def completed(self, completion_tokens: int) -> None:
        with self._lock:
            self._counters.requests_completed += 1
            self._counters.completion_tokens += completion_tokens

    def failed(self) -> None:
        with self._lock:
            self._counters.requests_failed += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return self._counters.as_dict()


def _configuration_verification(
    config: EngineConfig, observed: dict[str, object]
) -> dict[str, object]:
    declared = config.piq_configuration()
    missing_keys = sorted(set(declared) - set(observed))
    missing_observation_keys = sorted(
        key
        for key in (
            "gpu_name",
            "cuda_version",
            "torch_version",
            "transformers_version",
        )
        if not isinstance(observed.get(key), str) or not observed[key]
    )
    comparable_keys = set(declared) - set(missing_keys)
    mismatches = sorted(key for key in comparable_keys if declared.get(key) != observed.get(key))
    gpu_name = observed.get("gpu_name")
    if mismatches or (gpu_name is not None and "L40S" not in str(gpu_name)):
        status = "failed"
    elif missing_keys or missing_observation_keys:
        status = "not-observed"
    else:
        status = "passed"
    return {
        "status": status,
        "missingKeys": missing_keys,
        "missingObservationKeys": missing_observation_keys,
        "mismatchedKeys": mismatches,
        "verifiedAt": int(time.time()),
    }


def _usage(prompt_tokens: int, completion_tokens: int) -> dict[str, int]:
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _sse(value: dict[str, object] | str) -> bytes:
    payload = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    return f"data: {payload}\n\n".encode()


def create_app(engine: ServingEngine, config: EngineConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            close = getattr(engine, "close", None)
            if callable(close):
                await run_in_threadpool(close)

    app = FastAPI(title="ExactMap", version=RUNTIME_VERSION, lifespan=lifespan)
    state = RuntimeState()

    def begin(request: GenerationRequest) -> GenerationSession:
        try:
            session = engine.start(request.to_engine_input())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        state.started(session.prompt_tokens)
        return session

    def stream_response(
        request: GenerationRequest,
        session: GenerationSession,
        *,
        chat: bool,
    ) -> Iterator[bytes]:
        request_id = f"chatcmpl-{uuid.uuid4().hex}" if chat else f"cmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        completion_tokens = 0
        try:
            if chat:
                yield _sse(
                    {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": MODEL_ID,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant"},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            for token in session.tokens:
                completion_tokens += 1
                choice = (
                    {"index": 0, "delta": {"content": token.text}, "finish_reason": None}
                    if chat
                    else {"index": 0, "text": token.text, "finish_reason": None}
                )
                yield _sse(
                    {
                        "id": request_id,
                        "object": ("chat.completion.chunk" if chat else "text_completion"),
                        "created": created,
                        "model": MODEL_ID,
                        "choices": [choice],
                    }
                )
            finish_reason = "length" if completion_tokens >= request.max_tokens else "stop"
            terminal_choice = (
                {"index": 0, "delta": {}, "finish_reason": finish_reason}
                if chat
                else {"index": 0, "text": "", "finish_reason": finish_reason}
            )
            yield _sse(
                {
                    "id": request_id,
                    "object": "chat.completion.chunk" if chat else "text_completion",
                    "created": created,
                    "model": MODEL_ID,
                    "choices": [terminal_choice],
                }
            )
            if request.stream_options.include_usage:
                yield _sse(
                    {
                        "id": request_id,
                        "object": "chat.completion.chunk" if chat else "text_completion",
                        "created": created,
                        "model": MODEL_ID,
                        "choices": [],
                        "usage": _usage(session.prompt_tokens, completion_tokens),
                    }
                )
            state.completed(completion_tokens)
            yield _sse("[DONE]")
        except Exception:
            state.failed()
            raise

    async def nonstream_response(
        request: GenerationRequest,
        session: GenerationSession,
        *,
        chat: bool,
    ) -> dict[str, object]:
        try:
            tokens = await run_in_threadpool(list, session.tokens)
        except Exception:
            state.failed()
            raise
        text = "".join(token.text for token in tokens)
        completion_tokens = len(tokens)
        state.completed(completion_tokens)
        request_id = f"chatcmpl-{uuid.uuid4().hex}" if chat else f"cmpl-{uuid.uuid4().hex}"
        choice: dict[str, object]
        if chat:
            choice = {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": ("length" if completion_tokens >= request.max_tokens else "stop"),
            }
        else:
            choice = {
                "index": 0,
                "text": text,
                "finish_reason": ("length" if completion_tokens >= request.max_tokens else "stop"),
            }
        return {
            "id": request_id,
            "object": "chat.completion" if chat else "text_completion",
            "created": int(time.time()),
            "model": MODEL_ID,
            "choices": [choice],
            "usage": _usage(session.prompt_tokens, completion_tokens),
        }

    async def respond(request: GenerationRequest, *, chat: bool):
        session = await run_in_threadpool(begin, request)
        if request.stream:
            return StreamingResponse(
                stream_response(request, session, chat=chat),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )
        return await nonstream_response(request, session, chat=chat)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        if not engine.ready:
            raise HTTPException(status_code=503, detail="ExactMap is not ready")
        return {"status": "ready"}

    @app.get("/version")
    async def version() -> dict[str, str]:
        return {"product": RUNTIME_PRODUCT, "version": RUNTIME_VERSION}

    @app.get("/server_info")
    async def server_info() -> dict[str, object]:
        observed = engine.observed_configuration()
        verification = _configuration_verification(config, observed)
        return {
            "schemaVersion": "exactmap.server-info.v1",
            "runtime": {
                "engine": "custom",
                "product": RUNTIME_PRODUCT,
                "version": RUNTIME_VERSION,
                "profileId": PROFILE_ID,
                "profileVersion": PROFILE_VERSION,
            },
            "model": {
                "id": MODEL_ID,
                "revision": MODEL_REVISION,
                "weightsSha256": MODEL_WEIGHT_DIGEST,
            },
            "tokenizer": {
                "id": MODEL_ID,
                "revision": TOKENIZER_REVISION,
                "chatTemplate": "qwen3",
            },
            "artifact": {
                "engineBuildSha256": config.engine_build_sha256,
                "locator": config.artifact_locator,
            },
            "configuration": {
                "declared": config.piq_configuration(),
                "observed": observed,
                "verification": verification,
            },
            "capabilities": {
                "chatCompletions": True,
                "completions": True,
                "streaming": True,
                "streamUsage": True,
                "minTokens": True,
                "thinkingMode": True,
            },
            "debugCounters": state.snapshot(),
            "qualificationEligible": bool(
                config.engine_build_sha256
                and config.artifact_locator
                and config.kernel_family == "exactmap-triton-v1"
                and verification["status"] == "passed"
            ),
        }

    @app.get("/v1/models")
    async def models() -> dict[str, object]:
        return {
            "object": "list",
            "data": [
                {
                    "id": MODEL_ID,
                    "object": "model",
                    "owned_by": "Qwen",
                    "revision": MODEL_REVISION,
                }
            ],
        }

    @app.post("/v1/completions")
    async def completions(request: GenerationRequest):
        if request.prompt is None:
            raise HTTPException(status_code=422, detail="prompt is required")
        return await respond(request, chat=False)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: GenerationRequest):
        if request.messages is None:
            raise HTTPException(status_code=422, detail="messages are required")
        return await respond(request, chat=True)

    return app
