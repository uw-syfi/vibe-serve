from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

MODEL_ID = "Qwen/Qwen3-8B"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
TUNING_CORPUS_ID = "vibesys.exactmap.qwen3-8b.1k8k.tuning.v1"


@dataclass(frozen=True)
class RequestResult:
    request_index: int
    prompt_sha256: str
    prompt_tokens: int
    output_tokens: int
    total_latency: float
    ttft: float | None
    tpot: float | None
    finish_reason: str | None
    usage_exact: bool
    error: str | None


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def metric_summary(values: list[float], *, scale: float = 1) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "mean": sum(values) / len(values) * scale,
        "p50": float(percentile(values, 50)) * scale,
        "p90": float(percentile(values, 90)) * scale,
        "p95": float(percentile(values, 95)) * scale,
        "p99": float(percentile(values, 99)) * scale,
    }


def chat_token_count(tokenizer: Any, content: str) -> int:
    return len(
        tokenizer.apply_chat_template(
            [{"role": "user", "content": content}],
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=True,
        )
    )


def make_tuning_prompts(
    tokenizer: Any,
    *,
    target_tokens: int,
    pool_size: int,
    seed: int,
) -> list[str]:
    rng = random.Random(seed)
    prompts = []
    for index in range(pool_size):
        left = rng.randint(120, 980)
        right = rng.randint(20, 110)
        answer = left * right + index
        content = (
            "You are solving a VibeSys tuning problem. Show a complete derivation, "
            "check the result, and end with `FINAL: <integer>`. "
            f"Problem {index}: compute ({left} * {right}) + {index}. "
            f"The expected final integer is determined by the expression, not by context. "
        )
        padding_round = 0
        while chat_token_count(tokenizer, content) < target_tokens:
            content += (
                " This deterministic serving-shape datum is not a hint: "
                f"tuning-context-{index}-{padding_round}={answer + padding_round}."
            )
            padding_round += 1
        prompts.append(content)

    lengths = [chat_token_count(tokenizer, prompt) for prompt in prompts]
    minimum = math.floor(target_tokens * 0.8)
    maximum = math.ceil(target_tokens * 1.2)
    if any(length < minimum or length > maximum for length in lengths):
        raise RuntimeError(
            f"generated prompt lengths escape the 80-120% band: {min(lengths)}..{max(lengths)}"
        )
    return prompts


def stream_text(choice: dict[str, Any]) -> str:
    delta = choice.get("delta") or {}
    return (
        str(delta.get("reasoning_content") or "")
        + str(delta.get("content") or "")
        + str(choice.get("text") or "")
    )


async def request_once(
    client: httpx.AsyncClient,
    tokenizer: Any,
    *,
    url: str,
    prompt: str,
    request_index: int,
    min_tokens: int,
    max_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    timeout: float,
    seed: int,
) -> RequestResult:
    prompt_tokens = chat_token_count(tokenizer, prompt)
    body = {
        "model": MODEL_ID,
        "messages": [{"role": "user", "content": prompt}],
        "enable_thinking": True,
        "min_tokens": min_tokens,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "seed": seed + request_index,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    started = time.perf_counter()
    first_token_at = None
    finished_at = None
    finish_reason = None
    parts: list[str] = []
    usage = None
    saw_done = False
    error = None
    try:
        async with client.stream("POST", url, json=body, timeout=timeout) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise RuntimeError("response is not text/event-stream")
            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                payload = raw_line.removeprefix("data: ").strip()
                if payload == "[DONE]":
                    saw_done = True
                    finished_at = time.perf_counter()
                    break
                chunk = json.loads(payload)
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if choices:
                    choice = choices[0]
                    text = stream_text(choice)
                    if text:
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        parts.append(text)
                    if choice.get("finish_reason") is not None:
                        finish_reason = str(choice["finish_reason"])
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    if finished_at is None:
        finished_at = time.perf_counter()
    if error is None and not saw_done:
        error = "stream omitted [DONE]"

    output_text = "".join(parts)
    output_tokens = len(tokenizer.encode(output_text, add_special_tokens=False))
    usage_exact = usage == {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": prompt_tokens + output_tokens,
    }
    if error is None and output_tokens < min_tokens:
        error = f"output token floor missed: {output_tokens} < {min_tokens}"
    if error is None and not usage_exact:
        error = "stream usage differs from evaluator tokenization"
    if error is None and finish_reason not in {"stop", "length"}:
        error = f"invalid finish_reason: {finish_reason!r}"

    total_latency = finished_at - started
    ttft = None if first_token_at is None else first_token_at - started
    tpot = (
        None
        if first_token_at is None or output_tokens <= 1
        else (finished_at - first_token_at) / (output_tokens - 1)
    )
    return RequestResult(
        request_index=request_index,
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        total_latency=total_latency,
        ttft=ttft,
        tpot=tpot,
        finish_reason=finish_reason,
        usage_exact=usage_exact,
        error=error,
    )


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    response = await client.get(url, timeout=timeout)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"{url} did not return a JSON object")
    return value


async def warmup(
    client: httpx.AsyncClient,
    tokenizer: Any,
    args: argparse.Namespace,
    prompts: list[str],
    request_url: str,
) -> None:
    if args.warmup_requests == 0:
        return
    results = await asyncio.gather(
        *(
            request_once(
                client,
                tokenizer,
                url=request_url,
                prompt=prompts[index % len(prompts)],
                request_index=-(index + 1),
                min_tokens=min(args.warmup_max_tokens, args.min_tokens),
                max_tokens=args.warmup_max_tokens,
                temperature=args.temperature,
                top_p=args.top_p,
                top_k=args.top_k,
                timeout=args.timeout,
                seed=args.seed,
            )
            for index in range(args.warmup_requests)
        )
    )
    errors = [result.error for result in results if result.error]
    if errors:
        raise RuntimeError(f"warmup failed: {errors[0]}")


async def run_closed_loop(
    client: httpx.AsyncClient,
    tokenizer: Any,
    args: argparse.Namespace,
    prompts: list[str],
    request_url: str,
) -> list[RequestResult]:
    queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(args.num_requests):
        queue.put_nowait(index)
    results: list[RequestResult] = []
    results_lock = asyncio.Lock()
    deadline = None if args.duration <= 0 else time.perf_counter() + args.duration

    async def worker() -> None:
        while not queue.empty():
            if deadline is not None and time.perf_counter() >= deadline:
                return
            try:
                index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            result = await request_once(
                client,
                tokenizer,
                url=request_url,
                prompt=prompts[index % len(prompts)],
                request_index=index,
                min_tokens=args.min_tokens,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                top_p=args.top_p,
                top_k=args.top_k,
                timeout=args.timeout,
                seed=args.seed,
            )
            async with results_lock:
                results.append(result)
            queue.task_done()

    await asyncio.gather(*(worker() for _ in range(args.concurrency)))
    return sorted(results, key=lambda item: item.request_index)


async def run_open_loop(
    client: httpx.AsyncClient,
    tokenizer: Any,
    args: argparse.Namespace,
    prompts: list[str],
    request_url: str,
) -> list[RequestResult]:
    rng = random.Random(args.seed)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(index: int) -> RequestResult:
        async with semaphore:
            return await request_once(
                client,
                tokenizer,
                url=request_url,
                prompt=prompts[index % len(prompts)],
                request_index=index,
                min_tokens=args.min_tokens,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                top_p=args.top_p,
                top_k=args.top_k,
                timeout=args.timeout,
                seed=args.seed,
            )

    tasks = []
    started = time.perf_counter()
    for index in range(args.num_requests):
        if args.duration > 0 and time.perf_counter() - started >= args.duration:
            break
        if index > 0:
            await asyncio.sleep(rng.expovariate(args.rate))
        tasks.append(asyncio.create_task(one(index)))
    return sorted(await asyncio.gather(*tasks), key=lambda item: item.request_index)


def counter_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, int] | None:
    before_counters = before.get("debugCounters")
    after_counters = after.get("debugCounters")
    if not isinstance(before_counters, dict) or not isinstance(after_counters, dict):
        return None
    keys = (
        "requestsStarted",
        "requestsCompleted",
        "requestsFailed",
        "promptTokens",
        "completionTokens",
    )
    try:
        result = {key: int(after_counters[key]) - int(before_counters[key]) for key in keys}
    except (KeyError, TypeError, ValueError):
        return None
    return result if all(value >= 0 for value in result.values()) else None


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        args.tokenizer,
        revision=MODEL_REVISION,
        trust_remote_code=False,
    )
    prompts = make_tuning_prompts(
        tokenizer,
        target_tokens=args.prompt_len,
        pool_size=args.prompt_pool_size,
        seed=args.seed,
    )
    prompt_set_sha256 = canonical_sha256(
        {
            "schemaVersion": "vibesys.exactmap-tuning-prompts.v1",
            "corpusId": TUNING_CORPUS_ID,
            "seed": args.seed,
            "prompts": prompts,
        }
    )
    base_url = args.url.rstrip("/")
    request_url = base_url + args.endpoint
    limits = httpx.Limits(
        max_connections=max(args.concurrency * 2, 32),
        max_keepalive_connections=max(args.concurrency, 16),
    )

    async with httpx.AsyncClient(limits=limits) as client:
        health = await get_json(client, base_url + "/health")
        ready = await get_json(client, base_url + "/ready")
        models = await get_json(client, base_url + "/v1/models")
        if health != {"status": "ok"} or ready != {"status": "ready"}:
            raise RuntimeError("server health or readiness contract failed")
        model_entries = models.get("data")
        if (
            not isinstance(model_entries, list)
            or len(model_entries) != 1
            or model_entries[0].get("id") != MODEL_ID
            or model_entries[0].get("revision") != MODEL_REVISION
        ):
            raise RuntimeError("/v1/models does not expose the pinned model")

        await warmup(client, tokenizer, args, prompts, request_url)
        before = await get_json(client, base_url + "/server_info")
        measured_started = time.perf_counter()
        if args.rate > 0:
            results = await run_open_loop(client, tokenizer, args, prompts, request_url)
            arrival_model = "open-loop-poisson"
        else:
            results = await run_closed_loop(client, tokenizer, args, prompts, request_url)
            arrival_model = "closed-loop"
        measured_seconds = time.perf_counter() - measured_started
        after = await get_json(client, base_url + "/server_info")

    successes = [result for result in results if result.error is None]
    failures = [result for result in results if result.error is not None]
    total_output_tokens = sum(result.output_tokens for result in successes)
    total_prompt_tokens = sum(result.prompt_tokens for result in successes)
    latency = metric_summary([result.total_latency for result in successes], scale=1_000)
    ttft = metric_summary(
        [result.ttft for result in successes if result.ttft is not None],
        scale=1_000,
    )
    tpot = metric_summary(
        [result.tpot for result in successes if result.tpot is not None],
        scale=1_000,
    )
    counters = counter_delta(before, after)
    all_requests_completed = len(results) == args.num_requests
    counter_match = (
        counters is not None
        and counters["requestsStarted"] == len(results)
        and counters["requestsCompleted"] == len(successes)
        and counters["requestsFailed"] == len(failures)
    )
    hard_gates_passed = (
        all_requests_completed
        and not failures
        and len(successes) == args.num_requests
        and all(result.output_tokens >= args.min_tokens for result in successes)
        and all(result.usage_exact for result in successes)
        and counter_match
    )
    aggregate_throughput = total_output_tokens / measured_seconds if measured_seconds > 0 else 0
    p99_latency_ms = None if latency is None else latency["p99"]

    return {
        "schemaVersion": "vibesys.exactmap-benchmark-result.v1",
        "status": "passed" if hard_gates_passed else "failed",
        "model": {"id": MODEL_ID, "revision": MODEL_REVISION},
        "runtime": {"engine": "custom", "product": "ExactMap"},
        "workload": {
            "corpusId": TUNING_CORPUS_ID,
            "promptSetSha256": prompt_set_sha256,
            "sealedEvaluationCohortUsed": False,
            "arrivalModel": arrival_model,
            "concurrency": args.concurrency,
            "requestCount": args.num_requests,
            "promptTokensTarget": args.prompt_len,
            "minTokens": args.min_tokens,
            "maxTokens": args.max_tokens,
            "temperature": args.temperature,
            "topP": args.top_p,
            "topK": args.top_k,
            "seed": args.seed,
        },
        "num_requests": len(results),
        "num_completed": len(successes),
        "num_failed": len(failures),
        "actual_duration": measured_seconds,
        "total_prompt_tokens": total_prompt_tokens,
        "total_tokens": total_output_tokens,
        "aggregate_throughput": aggregate_throughput,
        "request_throughput": (len(successes) / measured_seconds if measured_seconds > 0 else 0),
        "p99_latency_ms": p99_latency_ms,
        "ttft": ttft,
        "tpot": tpot,
        "total_latency": latency,
        "hardGates": {
            "allRequestsCompleted": all_requests_completed,
            "zeroFailures": not failures,
            "outputFloor": all(result.output_tokens >= args.min_tokens for result in successes),
            "usageExact": all(result.usage_exact for result in successes),
            "debugCountersMatch": counter_match,
        },
        "serverInfoBefore": before,
        "serverInfoAfter": after,
        "debugCounterDelta": counters,
        "failures": [
            {
                "requestIndex": result.request_index,
                "promptSha256": result.prompt_sha256,
                "promptTokens": result.prompt_tokens,
                "outputTokens": result.output_tokens,
                "error": result.error,
            }
            for result in failures[:10]
        ],
        "requests": [
            {
                "requestIndex": result.request_index,
                "promptSha256": result.prompt_sha256,
                "promptTokens": result.prompt_tokens,
                "outputTokens": result.output_tokens,
                "latencyMs": result.total_latency * 1_000,
                "ttftMs": None if result.ttft is None else result.ttft * 1_000,
                "tpotMs": None if result.tpot is None else result.tpot * 1_000,
                "finishReason": result.finish_reason,
                "usageExact": result.usage_exact,
                "error": result.error,
            }
            for result in results
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark ExactMap on a controlled 1K-to-8K Qwen workload."
    )
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--endpoint", default="/v1/chat/completions")
    parser.add_argument("--tokenizer", default=MODEL_ID)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duration", type=float, default=0)
    parser.add_argument("--num-requests", type=int, default=32)
    parser.add_argument("--warmup-requests", type=int, default=4)
    parser.add_argument("--warmup-max-tokens", type=int, default=128)
    parser.add_argument("--min-tokens", type=int, default=4_096)
    parser.add_argument("--max-tokens", type=int, default=8_192)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--top-p", type=float, default=1)
    parser.add_argument("--top-k", type=int, default=0)
    parser.add_argument("--prompt-len", type=int, default=1_024)
    parser.add_argument("--prompt-pool-size", type=int, default=32)
    parser.add_argument("--rate", type=float, default=0)
    parser.add_argument("--seed", type=int, default=20_260_727)
    parser.add_argument("--timeout", type=float, default=1_800)
    parser.add_argument("--output-json")
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    if args.num_requests < args.concurrency:
        parser.error("--num-requests must be at least --concurrency")
    if args.min_tokens < 1 or args.min_tokens > args.max_tokens:
        parser.error("--min-tokens must be positive and not exceed --max-tokens")
    if args.max_tokens > 8_192:
        parser.error("--max-tokens must not exceed the frozen 8,192-token cap")
    if args.prompt_len < 819 or args.prompt_len > 1_229:
        parser.error("--prompt-len must remain inside the frozen 819-1,229 band")
    if args.rate < 0:
        parser.error("--rate must be zero for closed-loop or positive for Poisson")
    if args.duration < 0:
        parser.error("--duration must not be negative")
    return args


def main() -> None:
    args = parse_args()
    result = asyncio.run(run_benchmark(args))
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.write("\n")
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
