from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import torch

from .model import ExactMapQwen3, PagedKVCache
from .types import GeneratedToken, GenerationInput, GenerationSession

_DONE = object()


@dataclass(frozen=True)
class _Failure:
    error: BaseException


@dataclass
class _Job:
    request: GenerationInput
    input_ids: torch.Tensor
    prompt_tokens: int
    output: queue.Queue[object] = field(default_factory=queue.Queue)
    cancelled: threading.Event = field(default_factory=threading.Event)
    cache: PagedKVCache | None = None
    generator: torch.Generator | None = None
    generated_ids: list[int] = field(default_factory=list)
    previous_text: str = ""
    pending_token: int | None = None
    finished: bool = False


class ExactMapScheduler:
    """Decode-first continuous batch scheduler with deterministic cache ownership."""

    def __init__(
        self,
        model: ExactMapQwen3,
        tokenizer: Any,
        *,
        max_batch_size: int,
        eos_ids: tuple[int, ...],
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("ExactMap scheduler batch size must be positive")
        self._model = model
        self._tokenizer = tokenizer
        self._max_batch_size = max_batch_size
        self._eos_ids = eos_ids
        self._pending: deque[_Job] = deque()
        self._active: list[_Job] = []
        self._condition = threading.Condition()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="exactmap-decode-scheduler",
            daemon=True,
        )
        self._thread.start()

    @property
    def active_count(self) -> int:
        with self._condition:
            return len(self._active)

    @property
    def pending_count(self) -> int:
        with self._condition:
            return len(self._pending)

    def submit(
        self,
        request: GenerationInput,
        input_ids: torch.Tensor,
    ) -> GenerationSession:
        if input_ids.ndim != 2 or input_ids.shape[0] != 1:
            raise ValueError("ExactMap scheduler requires one prompt sequence")
        job = _Job(
            request=request,
            input_ids=input_ids,
            prompt_tokens=int(input_ids.shape[1]),
        )
        with self._condition:
            if self._closed:
                raise RuntimeError("ExactMap scheduler is closed")
            self._pending.append(job)
            self._condition.notify()
        return GenerationSession(
            prompt_tokens=job.prompt_tokens,
            tokens=self._stream(job),
        )

    def _stream(self, job: _Job) -> Iterator[GeneratedToken]:
        try:
            while True:
                item = job.output.get()
                if item is _DONE:
                    return
                if isinstance(item, _Failure):
                    raise RuntimeError("ExactMap scheduled generation failed") from item.error
                if not isinstance(item, GeneratedToken):
                    raise RuntimeError("ExactMap scheduler emitted an invalid queue item")
                yield item
        finally:
            job.cancelled.set()
            with self._condition:
                self._condition.notify()

    def _sample(self, logits: torch.Tensor, job: _Job) -> int:
        request = job.request
        if request.temperature == 0:
            return int(torch.argmax(logits, dim=-1).item())
        if job.generator is None:
            raise RuntimeError("ExactMap sampling generator is not initialized")
        scores = logits.float() / request.temperature
        if request.top_k > 0:
            top_k = min(request.top_k, scores.shape[-1])
            threshold = torch.topk(scores, top_k).values[-1]
            scores = torch.where(scores < threshold, -torch.inf, scores)
        if request.top_p < 1:
            sorted_scores, sorted_indices = torch.sort(scores, descending=True)
            probabilities = torch.softmax(sorted_scores, dim=-1)
            cumulative = torch.cumsum(probabilities, dim=-1)
            remove = cumulative - probabilities > request.top_p
            sorted_scores = sorted_scores.masked_fill(remove, -torch.inf)
            scores = torch.full_like(scores, -torch.inf).scatter(
                -1,
                sorted_indices,
                sorted_scores,
            )
        probabilities = torch.softmax(scores, dim=-1)
        return int(torch.multinomial(probabilities, 1, generator=job.generator).item())

    def _sample_batch(
        self,
        logits: torch.Tensor,
        jobs: list[_Job],
    ) -> list[int]:
        if all(job.request.temperature == 0 for job in jobs):
            suppress_eos = [len(job.generated_ids) < job.request.min_tokens for job in jobs]
            candidates = logits
            if any(suppress_eos):
                candidates = logits.clone()
                for index, suppress in enumerate(suppress_eos):
                    if suppress:
                        for eos_id in self._eos_ids:
                            candidates[index, eos_id] = -torch.inf
            return [int(value) for value in torch.argmax(candidates, dim=-1).tolist()]
        return [
            self._sample(self._suppress_eos(logits[index], job), job)
            for index, job in enumerate(jobs)
        ]

    def _suppress_eos(self, logits: torch.Tensor, job: _Job) -> torch.Tensor:
        if len(job.generated_ids) >= job.request.min_tokens:
            return logits
        result = logits.clone()
        for eos_id in self._eos_ids:
            result[eos_id] = -torch.inf
        return result

    def _accept_token(self, job: _Job, token_id: int) -> bool:
        index = len(job.generated_ids)
        if token_id in self._eos_ids and index >= job.request.min_tokens:
            self._finish(job)
            return False

        job.generated_ids.append(token_id)
        decoded = self._tokenizer.decode(
            job.generated_ids,
            skip_special_tokens=False,
            clean_up_tokenization_spaces=False,
        )
        text = (
            decoded[len(job.previous_text) :] if decoded.startswith(job.previous_text) else decoded
        )
        job.previous_text = decoded
        job.pending_token = token_id
        job.output.put(GeneratedToken(token_id=token_id, text=text, index=index))
        if len(job.generated_ids) >= job.request.max_tokens:
            self._finish(job)
            return False
        return True

    def _finish(self, job: _Job) -> None:
        if job.finished:
            return
        if job.cache is not None:
            job.cache.close()
            job.cache = None
        job.finished = True
        job.output.put(_DONE)

    def _fail(self, job: _Job, error: BaseException) -> None:
        if job.finished:
            return
        if job.cache is not None:
            job.cache.close()
            job.cache = None
        job.finished = True
        job.output.put(_Failure(error))
        job.output.put(_DONE)

    def _drop_cancelled(self) -> None:
        retained = []
        for job in self._active:
            if job.cancelled.is_set():
                self._finish(job)
            else:
                retained.append(job)
        self._active = retained
        while self._pending and self._pending[0].cancelled.is_set():
            self._finish(self._pending.popleft())

    def _admit_one(self) -> None:
        with self._condition:
            if len(self._active) >= self._max_batch_size or not self._pending:
                return
            job = self._pending.popleft()
        if job.cancelled.is_set():
            self._finish(job)
            return
        try:
            job.cache = self._model.new_cache()
            generator_device = (
                self._model.device.type
                if self._model.device.index is None
                else f"{self._model.device.type}:{self._model.device.index}"
            )
            job.generator = torch.Generator(device=generator_device)
            job.generator.manual_seed(job.request.seed)
            with torch.inference_mode():
                logits = self._model.prefill_sequence(
                    job.input_ids.to(self._model.device),
                    job.cache,
                )
            token_id = self._sample(self._suppress_eos(logits, job), job)
            if self._accept_token(job, token_id):
                self._active.append(job)
        except Exception as exc:
            self._fail(job, exc)

    def _decode_active(self) -> None:
        jobs = []
        for job in self._active:
            if job.cancelled.is_set():
                self._finish(job)
            elif job.pending_token is None:
                self._fail(job, RuntimeError("active ExactMap request has no decode token"))
            else:
                jobs.append(job)
        self._active = jobs
        if not jobs:
            return
        try:
            tokens = torch.tensor(
                [job.pending_token for job in jobs],
                device=self._model.device,
                dtype=torch.long,
            )
            caches = tuple(job.cache for job in jobs)
            if any(cache is None for cache in caches):
                raise RuntimeError("active ExactMap request lost its KV cache")
            with torch.inference_mode():
                logits = self._model.decode_batch(
                    tokens,
                    tuple(cache for cache in caches if cache is not None),
                )
            token_ids = self._sample_batch(logits, jobs)
        except Exception as exc:
            for job in jobs:
                self._fail(job, exc)
            self._active = []
            return

        retained = []
        for job, token_id in zip(jobs, token_ids, strict=True):
            if job.cancelled.is_set():
                self._finish(job)
            elif self._accept_token(job, token_id):
                retained.append(job)
        self._active = retained

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    while not self._closed and not self._pending and not self._active:
                        self._condition.wait()
                    if self._closed:
                        break
                self._drop_cancelled()
                self._decode_active()
                self._admit_one()
        except BaseException as exc:
            for job in [*self._active, *self._pending]:
                self._fail(job, exc)
            self._active.clear()
            self._pending.clear()
        finally:
            for job in [*self._active, *self._pending]:
                self._fail(job, RuntimeError("ExactMap scheduler stopped"))
            self._active.clear()
            self._pending.clear()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        self._thread.join()
