import argparse
import asyncio
import importlib.util
import random
import sys
from pathlib import Path

BENCHMARK_PATH = Path("examples/model-serving/Llama-3-8B/benchmark/benchmark.py")
SPEC = importlib.util.spec_from_file_location("llama_3_8b_benchmark", BENCHMARK_PATH)
assert SPEC is not None and SPEC.loader is not None
BENCHMARK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BENCHMARK
SPEC.loader.exec_module(BENCHMARK)


def test_closed_loop_connection_limit_matches_requested_concurrency():
    limits = BENCHMARK.http_limits_for(256)

    assert limits.max_connections == 256
    assert limits.max_keepalive_connections == 256


def test_open_loop_connection_limit_does_not_cap_active_requests():
    limits = BENCHMARK.http_limits_for(None)

    assert limits.max_connections is None
    assert limits.max_keepalive_connections == 100


def test_concurrency_stats_track_driver_and_response_streams_separately():
    stats = BENCHMARK.ConcurrencyStats()

    stats.request_started()
    stats.request_started()
    stats.stream_opened()
    stats.stream_closed()
    stats.request_finished()
    stats.request_finished()

    assert stats.max_in_flight_requests == 2
    assert stats.max_active_streams == 1
    assert stats.in_flight_requests == 0
    assert stats.active_streams == 0


def test_warmup_is_bounded_and_returns_discardable_results(monkeypatch):
    active = 0
    max_active = 0

    async def fake_send_request(
        client,
        url,
        model,
        prompt,
        max_tokens,
        temperature,
        concurrency_stats=None,
    ):
        nonlocal active, max_active
        assert concurrency_stats is None
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return {"error": None}

    monkeypatch.setattr(BENCHMARK, "send_request", fake_send_request)
    args = argparse.Namespace(
        warmup_requests=7,
        concurrency=3,
        model="model",
        max_tokens=128,
        temperature=0,
    )

    results = asyncio.run(
        BENCHMARK.run_warmup(
            object(),
            "http://127.0.0.1:8000/v1/completions",
            ["prompt"],
            args,
            random.Random(42),
        )
    )

    assert len(results) == 7
    assert max_active == 3


def test_benchmark_reports_requested_and_observed_concurrency(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, *, limits):
            self.limits = limits

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    async def fake_send_request(
        client,
        url,
        model,
        prompt,
        max_tokens,
        temperature,
        concurrency_stats=None,
    ):
        concurrency_stats.request_started()
        concurrency_stats.stream_opened()
        await asyncio.sleep(0)
        concurrency_stats.stream_closed()
        concurrency_stats.request_finished()
        return {
            "error": None,
            "output_tokens": 2,
            "finish_reason": "length",
            "total_latency": 0.01,
            "ttft": 0.001,
            "tpot": 0.009,
        }

    monkeypatch.setattr(BENCHMARK.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(BENCHMARK, "send_request", fake_send_request)
    args = argparse.Namespace(
        url="http://127.0.0.1:8000",
        endpoint="/v1/completions",
        model="model",
        rate=1.0,
        concurrency=2,
        duration=20.0,
        num_requests=2,
        max_tokens=128,
        temperature=0,
        warmup_requests=0,
        prompt_len=None,
        seed=42,
        output_json=None,
    )

    result = asyncio.run(BENCHMARK.run_benchmark(args))

    assert result["load_concurrency"] == {
        "requested_workers": 2,
        "client_max_connections": 2,
        "max_in_flight_requests": 2,
        "max_active_streams": 2,
    }
