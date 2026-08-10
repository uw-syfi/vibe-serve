from __future__ import annotations

import argparse
import asyncio
import json
import random
from typing import Any

import httpx
from jsonschema.validators import validator_for

from vs_bench.runner import run
from vs_bench.schedule import duration_limited
from vs_bench.stats import pct_block
from vs_bench.transport import stream_sse

PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer", "minimum": 0, "maximum": 120},
        "city": {"type": "string"},
        "interests": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 4,
        },
        "active": {"type": "boolean"},
    },
    "required": ["name", "age", "city", "interests", "active"],
    "additionalProperties": False,
}

PROMPTS = [
    "Return only valid JSON for a fictional profile of a software engineer.",
    "Return only valid JSON for a fictional profile of a teacher.",
    "Return only valid JSON for a fictional profile of a chef.",
    "Return only valid JSON for a fictional profile of a nurse.",
    "Return only valid JSON for a fictional profile of a musician.",
]


def validate(text: str) -> tuple[bool, bool, str | None]:
    try:
        value = json.loads(text)
    except Exception as exc:
        return False, False, str(exc)
    try:
        cls = validator_for(PROFILE_SCHEMA)
        cls.check_schema(PROFILE_SCHEMA)
        cls(PROFILE_SCHEMA).validate(value)
    except Exception as exc:
        return True, False, str(exc)
    return True, True, None


async def run_benchmark(args: argparse.Namespace) -> dict:
    rng = random.Random(args.seed)
    url = args.url.rstrip("/") + args.endpoint

    async with httpx.AsyncClient() as client:

        async def send(i: int) -> dict:
            prompt = rng.choice(PROMPTS)
            r = await stream_sse(
                client,
                url,
                {
                    "prompt": prompt,
                    "max_tokens": args.max_tokens,
                    "temperature": 0,
                    "stream": True,
                    "guided_json": PROFILE_SCHEMA,
                },
                timeout=args.timeout,
            )
            if r.error:
                return {
                    "error": r.error,
                    "total_latency": r.latency,
                    "ttft": None,
                    "tpot": None,
                    "output_tokens": r.token_count,
                    "parse_ok": False,
                    "schema_ok": False,
                }
            parse_ok, schema_ok, validation_error = validate(r.text)
            return {
                "error": None,
                "total_latency": r.latency,
                "ttft": r.ttft,
                "tpot": None
                if r.ttft is None or r.token_count <= 1
                else (r.latency - r.ttft) / (r.token_count - 1),
                "output_tokens": r.token_count,
                "parse_ok": parse_ok,
                "schema_ok": schema_ok,
                "validation_error": validation_error,
                "output_preview": r.text[:200],
            }

        result = await run(duration_limited(args.duration), send, concurrency=args.concurrency)

    transport_successes = [r for r in result.results if r["error"] is None]
    schema_successes = [r for r in transport_successes if r["schema_ok"]]
    errors = [r for r in result.results if r["error"] is not None]
    invalid = [r for r in transport_successes if not r["schema_ok"]]
    ttft = pct_block([r["ttft"] for r in schema_successes if r["ttft"] is not None], 1000.0)
    tpot = pct_block([r["tpot"] for r in schema_successes if r["tpot"] is not None], 1000.0)
    latency = pct_block([r["total_latency"] for r in schema_successes], 1000.0)
    total_tokens = sum(r["output_tokens"] for r in schema_successes)
    wall = result.wall_clock
    output = {
        "config": {
            "url": url,
            "concurrency": args.concurrency,
            "duration": args.duration,
            "max_tokens": args.max_tokens,
            "seed": args.seed,
        },
        "num_requests": len(result.results),
        "num_completed": len(transport_successes),
        "num_failed": len(errors),
        "parse_ok": sum(1 for r in transport_successes if r["parse_ok"]),
        "schema_ok": len(schema_successes),
        "schema_ok_frac": len(schema_successes) / len(transport_successes)
        if transport_successes
        else 0,
        "actual_duration": wall,
        "total_tokens": total_tokens,
        "aggregate_throughput": total_tokens / wall if wall > 0 else 0,
        "request_throughput": len(schema_successes) / wall if wall > 0 else 0,
        "ttft": ttft,
        "tpot": tpot,
        "total_latency": latency,
        "p99_ttft_ms": None if ttft is None else ttft["p99"],
        "p99_tpot_ms": None if tpot is None else tpot["p99"],
        "p99_latency_ms": None if latency is None else latency["p99"],
        "errors": errors[:5],
        "invalid": invalid[:5],
    }

    print(f"Transport completed {len(transport_successes)}/{len(result.results)} requests")
    print(f"Schema-valid responses: {len(schema_successes)}/{len(transport_successes)}")
    print(f"Schema-valid throughput: {output['aggregate_throughput']:.2f} tok/s")
    if output["p99_latency_ms"] is not None:
        print(f"p99 latency: {output['p99_latency_ms']:.2f} ms")
    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(output, f, indent=2)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Constrained JSON vLLM benchmark.")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--endpoint", default="/v1/completions")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--max-tokens", type=int, default=96)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
