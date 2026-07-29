from __future__ import annotations

import argparse
import asyncio
import json
import random
import string
from dataclasses import dataclass
from typing import Any

import httpx

MODEL_ID = "Qwen/Qwen3-8B"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
MODEL_WEIGHT_DIGEST = "sha256:8f51132290852a4ab4070da7e075f9a6e14f2e14553663e25211fdd99c170222"
TOKENIZER_REVISION = MODEL_REVISION


@dataclass(frozen=True)
class StreamResult:
    text: str
    usage: dict[str, int] | None
    finish_reason: str | None
    error: str | None


def _content(choice: dict[str, Any]) -> str:
    delta = choice.get("delta") or {}
    return (
        str(delta.get("reasoning_content") or "")
        + str(delta.get("content") or "")
        + str(choice.get("text") or "")
    )


async def stream_request(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    timeout: float,
) -> StreamResult:
    parts: list[str] = []
    usage = None
    finish_reason = None
    saw_done = False
    try:
        async with client.stream("POST", url, json=body, timeout=timeout) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                return StreamResult("", None, None, "response is not text/event-stream")
            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                payload = raw_line.removeprefix("data: ").strip()
                if payload == "[DONE]":
                    saw_done = True
                    break
                chunk = json.loads(payload)
                chunk_usage = chunk.get("usage")
                if isinstance(chunk_usage, dict):
                    usage = {
                        "prompt_tokens": int(chunk_usage["prompt_tokens"]),
                        "completion_tokens": int(chunk_usage["completion_tokens"]),
                        "total_tokens": int(chunk_usage["total_tokens"]),
                    }
                choices = chunk.get("choices") or []
                if choices:
                    choice = choices[0]
                    parts.append(_content(choice))
                    if choice.get("finish_reason") is not None:
                        finish_reason = str(choice["finish_reason"])
    except Exception as exc:  # noqa: BLE001
        return StreamResult("".join(parts), usage, finish_reason, f"{type(exc).__name__}: {exc}")
    if not saw_done:
        return StreamResult("".join(parts), usage, finish_reason, "stream omitted [DONE]")
    return StreamResult("".join(parts), usage, finish_reason, None)


async def json_request(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    timeout: float,
    *,
    chat: bool,
) -> StreamResult:
    try:
        response = await client.post(url, json=body, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or len(choices) != 1:
            raise RuntimeError("response must contain exactly one choice")
        choice = choices[0]
        if chat:
            message = choice.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise RuntimeError("chat response omitted the assistant message")
            text = str(message.get("content") or "")
        else:
            text = str(choice.get("text") or "")
        raw_usage = payload.get("usage")
        if not isinstance(raw_usage, dict):
            raise RuntimeError("response omitted usage")
        usage = {
            "prompt_tokens": int(raw_usage["prompt_tokens"]),
            "completion_tokens": int(raw_usage["completion_tokens"]),
            "total_tokens": int(raw_usage["total_tokens"]),
        }
        finish_reason = choice.get("finish_reason")
        return StreamResult(
            text=text,
            usage=usage,
            finish_reason=None if finish_reason is None else str(finish_reason),
            error=None,
        )
    except Exception as exc:  # noqa: BLE001
        return StreamResult("", None, None, f"{type(exc).__name__}: {exc}")


def completion_token_count(tokenizer: Any, text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=False))


def chat_prompt_token_count(
    tokenizer: Any,
    messages: list[dict[str, str]],
    *,
    enable_thinking: bool,
) -> int:
    tokens = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=enable_thinking,
    )
    return len(tokens)


def usage_is_exact(
    result: StreamResult,
    *,
    prompt_tokens: int,
    completion_tokens: int,
) -> bool:
    return result.usage == {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


async def check_runtime_contract(
    client: httpx.AsyncClient,
    base_url: str,
) -> list[dict[str, Any]]:
    checks = []
    for path, expected_status in (("/health", "ok"), ("/ready", "ready")):
        try:
            response = await client.get(base_url + path, timeout=30)
            response.raise_for_status()
            body = response.json()
            ok = body == {"status": expected_status}
            error = None
        except Exception as exc:  # noqa: BLE001
            body = None
            ok = False
            error = f"{type(exc).__name__}: {exc}"
        checks.append({"check": path, "ok": ok, "body": body, "error": error})

    try:
        version_response = await client.get(base_url + "/version", timeout=30)
        version_response.raise_for_status()
        version = version_response.json()
        version_ok = version == {"product": "ExactMap", "version": "0.1.0"}
        version_error = None
    except Exception as exc:  # noqa: BLE001
        version = None
        version_ok = False
        version_error = f"{type(exc).__name__}: {exc}"
    checks.append(
        {
            "check": "/version",
            "ok": version_ok,
            "body": version,
            "error": version_error,
        }
    )

    try:
        models_response = await client.get(base_url + "/v1/models", timeout=30)
        models_response.raise_for_status()
        models = models_response.json()
        entries = models.get("data") if isinstance(models, dict) else None
        models_ok = (
            isinstance(entries, list)
            and len(entries) == 1
            and entries[0].get("id") == MODEL_ID
            and entries[0].get("revision") == MODEL_REVISION
        )
        models_error = None
    except Exception as exc:  # noqa: BLE001
        models = None
        models_ok = False
        models_error = f"{type(exc).__name__}: {exc}"
    checks.append(
        {
            "check": "/v1/models",
            "ok": models_ok,
            "body": models,
            "error": models_error,
        }
    )

    try:
        info_response = await client.get(base_url + "/server_info", timeout=30)
        info_response.raise_for_status()
        info = info_response.json()
        runtime = info.get("runtime") or {}
        model = info.get("model") or {}
        tokenizer = info.get("tokenizer") or {}
        artifact = info.get("artifact") or {}
        configuration = info.get("configuration") or {}
        capabilities = info.get("capabilities") or {}
        eligible = info.get("qualificationEligible")
        digest = artifact.get("engineBuildSha256")
        locator = artifact.get("locator")
        artifact_honest = (eligible is False and digest is None and locator is None) or (
            eligible is True
            and isinstance(digest, str)
            and digest.startswith("sha256:")
            and len(digest) == 71
            and isinstance(locator, str)
            and bool(locator)
        )
        info_ok = (
            info.get("schemaVersion") == "exactmap.server-info.v1"
            and runtime
            == {
                "engine": "custom",
                "product": "ExactMap",
                "version": "0.1.0",
                "profileId": "exactmap",
                "profileVersion": "exactmap.v1",
            }
            and model
            == {
                "id": MODEL_ID,
                "revision": MODEL_REVISION,
                "weightsSha256": MODEL_WEIGHT_DIGEST,
            }
            and tokenizer
            == {
                "id": MODEL_ID,
                "revision": TOKENIZER_REVISION,
                "chatTemplate": "qwen3",
            }
            and set(configuration) == {"declared", "observed", "verification"}
            and isinstance(configuration["declared"], dict)
            and isinstance(configuration["observed"], dict)
            and (configuration["verification"] or {}).get("status")
            in {"passed", "failed", "not-observed"}
            and all(
                capabilities.get(name) is True
                for name in (
                    "chatCompletions",
                    "completions",
                    "streaming",
                    "streamUsage",
                    "minTokens",
                    "thinkingMode",
                )
            )
            and isinstance(info.get("debugCounters"), dict)
            and isinstance(eligible, bool)
            and artifact_honest
        )
        info_error = None
    except Exception as exc:  # noqa: BLE001
        info = None
        info_ok = False
        info_error = f"{type(exc).__name__}: {exc}"
    checks.append(
        {
            "check": "/server_info",
            "ok": info_ok,
            "body": info,
            "error": info_error,
        }
    )
    return checks


async def check_chat_case(
    client: httpx.AsyncClient,
    tokenizer: Any,
    base_url: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    min_tokens: int = 0,
    enable_thinking: bool = False,
    seed: int = 0,
    timeout: float,
) -> tuple[StreamResult, int]:
    body = {
        "model": MODEL_ID,
        "messages": messages,
        "enable_thinking": enable_thinking,
        "min_tokens": min_tokens,
        "max_tokens": max_tokens,
        "temperature": 0,
        "top_p": 1,
        "top_k": 0,
        "seed": seed,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    result = await stream_request(
        client,
        base_url + "/v1/chat/completions",
        body,
        timeout,
    )
    prompt_tokens = chat_prompt_token_count(
        tokenizer,
        messages,
        enable_thinking=enable_thinking,
    )
    return result, prompt_tokens


async def run_checks(args: argparse.Namespace) -> dict[str, Any]:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        args.tokenizer,
        revision=MODEL_REVISION,
        trust_remote_code=False,
    )
    base_url = args.url.rstrip("/")
    rng = random.Random(args.seed)
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        results.extend(await check_runtime_contract(client, base_url))

        completion_prompt = (
            "Continue this response with at least one useful token: "
            "deterministic runtime validation"
        )
        completion_prompt_tokens = len(
            tokenizer.encode(completion_prompt, add_special_tokens=False)
        )
        completion_body = {
            "model": MODEL_ID,
            "prompt": completion_prompt,
            "min_tokens": 1,
            "max_tokens": 32,
            "temperature": 0,
            "top_p": 1,
            "top_k": 0,
            "seed": args.seed,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        streaming_completion = await stream_request(
            client,
            base_url + "/v1/completions",
            completion_body,
            args.timeout,
        )
        streaming_completion_tokens = completion_token_count(tokenizer, streaming_completion.text)
        results.append(
            {
                "check": "streaming-completions",
                "ok": (
                    streaming_completion.error is None
                    and streaming_completion_tokens > 0
                    and streaming_completion.finish_reason in {"stop", "length"}
                    and usage_is_exact(
                        streaming_completion,
                        prompt_tokens=completion_prompt_tokens,
                        completion_tokens=streaming_completion_tokens,
                    )
                ),
                "output": streaming_completion.text,
                "usage": streaming_completion.usage,
                "error": streaming_completion.error,
            }
        )

        nonstream_completion = await json_request(
            client,
            base_url + "/v1/completions",
            {**completion_body, "stream": False},
            args.timeout,
            chat=False,
        )
        nonstream_completion_tokens = completion_token_count(tokenizer, nonstream_completion.text)
        results.append(
            {
                "check": "nonstream-completions",
                "ok": (
                    nonstream_completion.error is None
                    and nonstream_completion_tokens > 0
                    and nonstream_completion.finish_reason in {"stop", "length"}
                    and usage_is_exact(
                        nonstream_completion,
                        prompt_tokens=completion_prompt_tokens,
                        completion_tokens=nonstream_completion_tokens,
                    )
                ),
                "output": nonstream_completion.text,
                "usage": nonstream_completion.usage,
                "error": nonstream_completion.error,
            }
        )

        nonstream_messages = [
            {
                "role": "user",
                "content": "Reply with a short confirmation that this chat endpoint works.",
            }
        ]
        nonstream_chat_body = {
            "model": MODEL_ID,
            "messages": nonstream_messages,
            "enable_thinking": False,
            "min_tokens": 1,
            "max_tokens": 32,
            "temperature": 0,
            "top_p": 1,
            "top_k": 0,
            "seed": args.seed,
            "stream": False,
            "stream_options": {"include_usage": True},
        }
        nonstream_chat = await json_request(
            client,
            base_url + "/v1/chat/completions",
            nonstream_chat_body,
            args.timeout,
            chat=True,
        )
        nonstream_chat_prompt_tokens = chat_prompt_token_count(
            tokenizer,
            nonstream_messages,
            enable_thinking=False,
        )
        nonstream_chat_completion_tokens = completion_token_count(tokenizer, nonstream_chat.text)
        results.append(
            {
                "check": "nonstream-chat-completions",
                "ok": (
                    nonstream_chat.error is None
                    and nonstream_chat_completion_tokens > 0
                    and nonstream_chat.finish_reason in {"stop", "length"}
                    and usage_is_exact(
                        nonstream_chat,
                        prompt_tokens=nonstream_chat_prompt_tokens,
                        completion_tokens=nonstream_chat_completion_tokens,
                    )
                ),
                "output": nonstream_chat.text,
                "usage": nonstream_chat.usage,
                "error": nonstream_chat.error,
            }
        )

        for index in range(args.sentinel_cases):
            sentinel = "".join(rng.choice(string.ascii_uppercase) for _ in range(10))
            messages = [
                {
                    "role": "user",
                    "content": (f"Return this exact uppercase marker and nothing else: {sentinel}"),
                }
            ]
            result, prompt_tokens = await check_chat_case(
                client,
                tokenizer,
                base_url,
                messages,
                max_tokens=32,
                seed=args.seed + index,
                timeout=args.timeout,
            )
            output_tokens = completion_token_count(tokenizer, result.text)
            results.append(
                {
                    "check": f"sentinel-{index}",
                    "ok": (
                        result.error is None
                        and sentinel in result.text.upper()
                        and usage_is_exact(
                            result,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=output_tokens,
                        )
                    ),
                    "output": result.text,
                    "usage": result.usage,
                    "error": result.error,
                }
            )

        known_answers = (
            ("What is 17 multiplied by 6? Answer with one number.", ("102",)),
            ("What is the capital of France? Answer with one word.", ("paris",)),
            ("Complete the sequence: 3, 6, 9, 12, ... Answer with one number.", ("15",)),
            ("What chemical formula represents water? Answer with one formula.", ("h2o", "h₂o")),
        )
        for index, (question, accepted) in enumerate(known_answers):
            messages = [{"role": "user", "content": question}]
            result, prompt_tokens = await check_chat_case(
                client,
                tokenizer,
                base_url,
                messages,
                max_tokens=32,
                seed=args.seed + 100 + index,
                timeout=args.timeout,
            )
            output_tokens = completion_token_count(tokenizer, result.text)
            results.append(
                {
                    "check": f"known-answer-{index}",
                    "ok": (
                        result.error is None
                        and any(answer in result.text.lower() for answer in accepted)
                        and usage_is_exact(
                            result,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=output_tokens,
                        )
                    ),
                    "output": result.text,
                    "usage": result.usage,
                    "error": result.error,
                }
            )

        deterministic_messages = [
            {
                "role": "user",
                "content": (
                    "Explain why the sum of the first five positive integers is 15 "
                    "in two concise sentences."
                ),
            }
        ]
        first, first_prompt_tokens = await check_chat_case(
            client,
            tokenizer,
            base_url,
            deterministic_messages,
            max_tokens=96,
            seed=args.seed + 200,
            timeout=args.timeout,
        )
        second, second_prompt_tokens = await check_chat_case(
            client,
            tokenizer,
            base_url,
            deterministic_messages,
            max_tokens=96,
            seed=args.seed + 999,
            timeout=args.timeout,
        )
        first_tokens = tokenizer.encode(first.text, add_special_tokens=False)
        second_tokens = tokenizer.encode(second.text, add_special_tokens=False)
        results.append(
            {
                "check": "greedy-determinism",
                "ok": (
                    first.error is None
                    and second.error is None
                    and bool(first_tokens)
                    and first_tokens == second_tokens
                    and usage_is_exact(
                        first,
                        prompt_tokens=first_prompt_tokens,
                        completion_tokens=len(first_tokens),
                    )
                    and usage_is_exact(
                        second,
                        prompt_tokens=second_prompt_tokens,
                        completion_tokens=len(second_tokens),
                    )
                ),
                "firstOutput": first.text,
                "secondOutput": second.text,
                "error": first.error or second.error,
            }
        )

        floor_messages = [
            {
                "role": "user",
                "content": (
                    "List distinct observations about deterministic software testing. "
                    "Continue until the requested token floor is reached."
                ),
            }
        ]
        floor, floor_prompt_tokens = await check_chat_case(
            client,
            tokenizer,
            base_url,
            floor_messages,
            min_tokens=args.floor_tokens,
            max_tokens=args.floor_tokens + 32,
            seed=args.seed + 300,
            timeout=args.timeout,
        )
        floor_count = completion_token_count(tokenizer, floor.text)
        results.append(
            {
                "check": "min-token-floor",
                "ok": (
                    floor.error is None
                    and floor_count >= args.floor_tokens
                    and usage_is_exact(
                        floor,
                        prompt_tokens=floor_prompt_tokens,
                        completion_tokens=floor_count,
                    )
                ),
                "completionTokens": floor_count,
                "usage": floor.usage,
                "error": floor.error,
            }
        )

        thinking_messages = [
            {
                "role": "user",
                "content": "Reason step by step: if x + 7 = 19, what is x?",
            }
        ]
        thinking, thinking_prompt_tokens = await check_chat_case(
            client,
            tokenizer,
            base_url,
            thinking_messages,
            max_tokens=128,
            enable_thinking=True,
            seed=args.seed + 400,
            timeout=args.timeout,
        )
        thinking_count = completion_token_count(tokenizer, thinking.text)
        results.append(
            {
                "check": "thinking-mode",
                "ok": (
                    thinking.error is None
                    and thinking_count > 0
                    and "12" in thinking.text
                    and usage_is_exact(
                        thinking,
                        prompt_tokens=thinking_prompt_tokens,
                        completion_tokens=thinking_count,
                    )
                ),
                "output": thinking.text,
                "usage": thinking.usage,
                "error": thinking.error,
            }
        )

    failures = [item for item in results if item["ok"] is not True]
    return {
        "schemaVersion": "exactmap.accuracy-check.v1",
        "model": {"id": MODEL_ID, "revision": MODEL_REVISION},
        "status": "passed" if not failures else "failed",
        "checks": results,
        "failureCount": len(failures),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check ExactMap API and output correctness.")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--tokenizer", default=MODEL_ID)
    parser.add_argument("--seed", type=int, default=8128)
    parser.add_argument("--sentinel-cases", type=int, default=3)
    parser.add_argument("--floor-tokens", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output-json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(run_checks(args))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.write("\n")
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
