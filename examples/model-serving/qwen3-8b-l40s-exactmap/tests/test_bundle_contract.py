from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from vibesys.input_manifest import load_input_bundle

PROJECT_ROOT = Path(__file__).parents[4]
BUNDLE_ROOT = Path(__file__).parents[1]
STARTER_ROOT = PROJECT_ROOT / "examples" / "starters" / "exactmap-l40s"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ShapeTokenizer:
    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool,
        add_generation_prompt: bool,
        enable_thinking: bool,
    ) -> list[int]:
        assert tokenize is True
        assert add_generation_prompt is True
        assert enable_thinking is True
        content = messages[0]["content"]
        return list(range(max(1, len(content.split()))))


def test_input_bundle_resolves_exactmap_starter_and_metric() -> None:
    bundle = load_input_bundle(BUNDLE_ROOT, project_root=PROJECT_ROOT)

    assert bundle.workspace_seed_path == STARTER_ROOT
    assert bundle.domain == "llm-serving"
    assert bundle.benchmark_result is not None
    assert bundle.benchmark_result.metric == "aggregate_throughput"
    assert bundle.accuracy_command[-1] == "accuracy_checker/checker.py"
    assert bundle.benchmark_command[-1] == "benchmark/benchmark.py"


def test_reference_pins_the_expected_qwen_identity() -> None:
    meta = json.loads((BUNDLE_ROOT / "reference" / "meta.json").read_text())
    config = json.loads((BUNDLE_ROOT / "reference" / "config.json").read_text())

    assert meta == {
        "model_id": "Qwen/Qwen3-8B",
        "revision": MODEL_REVISION,
        "weight_sha256": ("8f51132290852a4ab4070da7e075f9a6e14f2e14553663e25211fdd99c170222"),
        "task": "text-generation",
    }
    assert config["architectures"] == ["Qwen3ForCausalLM"]
    assert config["hidden_size"] == 4096
    assert config["num_hidden_layers"] == 36
    assert config["num_attention_heads"] == 32
    assert config["num_key_value_heads"] == 8
    assert config["torch_dtype"] == "bfloat16"


def test_tuning_prompts_are_deterministic_and_shape_bounded() -> None:
    benchmark = load_module(
        "exactmap_bundle_benchmark",
        BUNDLE_ROOT / "benchmark" / "benchmark.py",
    )
    tokenizer = ShapeTokenizer()

    first = benchmark.make_tuning_prompts(
        tokenizer,
        target_tokens=256,
        pool_size=4,
        seed=17,
    )
    second = benchmark.make_tuning_prompts(
        tokenizer,
        target_tokens=256,
        pool_size=4,
        seed=17,
    )
    lengths = [benchmark.chat_token_count(tokenizer, prompt) for prompt in first]

    assert first == second
    assert len(set(first)) == 4
    assert all(204 <= length <= 308 for length in lengths)
    assert all("tuning-context-" in prompt for prompt in first)


def test_counter_delta_rejects_missing_or_decreasing_values() -> None:
    benchmark = load_module(
        "exactmap_bundle_counters",
        BUNDLE_ROOT / "benchmark" / "benchmark.py",
    )
    before = {
        "debugCounters": {
            "requestsStarted": 4,
            "requestsCompleted": 4,
            "requestsFailed": 0,
            "promptTokens": 40,
            "completionTokens": 400,
        }
    }
    after = {
        "debugCounters": {
            "requestsStarted": 7,
            "requestsCompleted": 7,
            "requestsFailed": 0,
            "promptTokens": 70,
            "completionTokens": 700,
        }
    }

    assert benchmark.counter_delta(before, after) == {
        "requestsStarted": 3,
        "requestsCompleted": 3,
        "requestsFailed": 0,
        "promptTokens": 30,
        "completionTokens": 300,
    }
    assert benchmark.counter_delta(before, {}) is None

    decreasing: dict[str, Any] = json.loads(json.dumps(after))
    decreasing["debugCounters"]["requestsStarted"] = 1
    assert benchmark.counter_delta(before, decreasing) is None


def test_objective_keeps_tuning_and_governed_evaluation_separate() -> None:
    objective = " ".join((BUNDLE_ROOT / "OBJECTIVE.md").read_text().split())
    integration = " ".join((BUNDLE_ROOT / "PIQ_INTEGRATION.md").read_text().split())

    assert "must not read or reuse a PIQ campaign prompt manifest" in objective
    assert "only the governed PIQ campaign" in integration
    assert "`producer.engine=custom`" in integration
    assert "Unknown `custom` products must remain fail-closed" in integration
