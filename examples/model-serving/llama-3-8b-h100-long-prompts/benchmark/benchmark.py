from __future__ import annotations

import argparse
import asyncio
import json
import random

import httpx

from vs_bench.runner import run
from vs_bench.schedule import duration_limited
from vs_bench.stats import pct_block
from vs_bench.transport import stream_sse


def prompts(prompt_len: int, pool_size: int) -> list[str]:
    base = " ".join(f"fact{i % 97}" for i in range(prompt_len))
    return [base for _ in range(pool_size)]


async def run_benchmark(args: argparse.Namespace) -> dict:
    rng = random.Random(args.seed)
    prompt_pool = prompts(args.prompt_len, args.prompt_pool_size)
    url = args.url.rstrip("/") + args.endpoint

    async with httpx.AsyncClient() as client:

        async def send(i: int) -> dict:
            prompt = rng.choice(prompt_pool)
            r = await stream_sse(
                client,
                url,
                {
                    "prompt": prompt,
                    "max_tokens": args.max_tokens,
                    "temperature": args.temperature,
                    "stream": True,
                },
                timeout=args.timeout,
            )
            return {
                "error": r.error,
                "total_latency": r.latency,
                "ttft": r.ttft,
                "tpot": None
                if r.ttft is None or r.token_count <= 1
                else (r.latency - r.ttft) / (r.token_count - 1),
                "output_tokens": r.token_count,
            }

        result = await run(duration_limited(args.duration), send, concurrency=args.concurrency)

    successes = [r for r in result.results if r["error"] is None]
    errors = [r for r in result.results if r["error"] is not None]
    ttft = pct_block([r["ttft"] for r in successes if r["ttft"] is not None], 1000.0)
    tpot = pct_block([r["tpot"] for r in successes if r["tpot"] is not None], 1000.0)
    latency = pct_block([r["total_latency"] for r in successes], 1000.0)
    total_tokens = sum(r["output_tokens"] for r in successes)
    output = {
        "config": {
            "url": url,
            "concurrency": args.concurrency,
            "duration": args.duration,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
            "prompt_len": args.prompt_len,
            "prompt_pool_size": args.prompt_pool_size,
            "seed": args.seed,
        },
        "num_requests": len(result.results),
        "num_completed": len(successes),
        "num_failed": len(errors),
        "actual_duration": result.wall_clock,
        "total_tokens": total_tokens,
        "aggregate_throughput": total_tokens / result.wall_clock if result.wall_clock > 0 else 0,
        "request_throughput": len(successes) / result.wall_clock if result.wall_clock > 0 else 0,
        "ttft": ttft,
        "tpot": tpot,
        "total_latency": latency,
        "p99_ttft_ms": None if ttft is None else ttft["p99"],
        "p99_tpot_ms": None if tpot is None else tpot["p99"],
        "p99_latency_ms": None if latency is None else latency["p99"],
        "errors": errors[:5],
    }

    print(f"Completed {len(successes)}/{len(result.results)} requests")
    print(f"Aggregate throughput: {output['aggregate_throughput']:.2f} tok/s")
    if output["p99_latency_ms"] is not None:
        print(f"p99 latency: {output['p99_latency_ms']:.2f} ms")
    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(output, f, indent=2)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Long-prompt short-output vLLM benchmark.")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--endpoint", default="/v1/completions")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--prompt-len", type=int, default=3000)
    parser.add_argument("--prompt-pool-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
