from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from exactmap.api import create_app
from exactmap.config import MODEL_WEIGHT_DIGEST, TOKENIZER_REVISION, EngineConfig
from exactmap.types import GeneratedToken, GenerationInput, GenerationSession
from fastapi.testclient import TestClient
from pydantic import ValidationError


class FakeEngine:
    ready = True

    def start(self, request: GenerationInput) -> GenerationSession:
        assert request.max_tokens >= 2

        def tokens() -> Iterator[GeneratedToken]:
            yield GeneratedToken(token_id=1, text="hello", index=0)
            yield GeneratedToken(token_id=2, text=" world", index=1)

        return GenerationSession(prompt_tokens=7, tokens=tokens())

    def observed_configuration(self) -> dict[str, object]:
        return {
            **EngineConfig().piq_configuration(),
            "gpu_name": "NVIDIA L40S",
            "cuda_version": "12.8",
            "torch_version": "2.7.0",
            "transformers_version": "4.53.2",
        }


class UnobservedEngine(FakeEngine):
    def observed_configuration(self) -> dict[str, object]:
        return EngineConfig().piq_configuration()


class WrongGpuEngine(FakeEngine):
    def observed_configuration(self) -> dict[str, object]:
        return {
            **EngineConfig().piq_configuration(),
            "gpu_name": "NVIDIA H100 80GB HBM3",
        }


class PartialObservationEngine(FakeEngine):
    def observed_configuration(self) -> dict[str, object]:
        return {"gpu_name": "NVIDIA L40S"}


class BootstrapFamilyEngine(FakeEngine):
    def observed_configuration(self) -> dict[str, object]:
        return {
            **EngineConfig(kernel_family="bootstrap-transformers").piq_configuration(),
            "gpu_name": "NVIDIA L40S",
            "cuda_version": "12.8",
            "torch_version": "2.7.0",
            "transformers_version": "4.53.2",
        }


class ClosableEngine(FakeEngine):
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def sealed_config() -> EngineConfig:
    return EngineConfig(
        engine_build_sha256="sha256:" + ("a" * 64),
        artifact_locator="oci://registry.example/exactmap@sha256:" + ("b" * 64),
    )


def test_config_is_closed_and_build_digest_is_strict() -> None:
    with pytest.raises(ValidationError, match="extra"):
        EngineConfig(unknown_knob=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="engine_build_sha256"):
        EngineConfig(engine_build_sha256="latest")
    with pytest.raises(ValidationError, match="artifact_locator"):
        EngineConfig(artifact_locator="oci://registry.example/exactmap:latest")
    with pytest.raises(ValidationError, match="kernel_family"):
        EngineConfig(kernel_family="pretend-fast")  # type: ignore[arg-type]


def test_starter_dependency_and_l40s_target_contract() -> None:
    root = Path(__file__).parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    dependencies = set(pyproject["project"]["dependencies"])
    modal_source = (root / "modal_app.py").read_text()

    assert any(dependency.startswith("torch>=") for dependency in dependencies)
    assert any(dependency.startswith("triton>=") for dependency in dependencies)
    assert any(dependency.startswith("huggingface-hub>=") for dependency in dependencies)
    assert "transformers==4.53.2" in dependencies
    assert not any(
        engine in dependency.lower()
        for engine in ("vllm", "sglang", "tensorrt-llm")
        for dependency in dependencies
    )
    assert 'gpu="L40S"' in modal_source


def test_default_config_describes_the_real_first_kernel_specialization() -> None:
    config = EngineConfig()

    assert config.kernel_family == "exactmap-triton-v1"
    assert config.max_batch_size == 16
    assert config.max_num_batched_tokens == 147_456
    assert config.chunked_prefill_size == 0
    assert config.cuda_graph_batch_sizes == "none"

    with pytest.raises(ValidationError, match="max_batch_size"):
        EngineConfig(max_batch_size=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="chunked_prefill_size"):
        EngineConfig(chunked_prefill_size=1_024)  # type: ignore[arg-type]


def test_server_info_binds_product_profile_artifact_and_observation() -> None:
    client = TestClient(create_app(FakeEngine(), sealed_config()))
    response = client.get("/server_info")
    response.raise_for_status()
    value = response.json()

    assert value["runtime"] == {
        "engine": "custom",
        "product": "ExactMap",
        "version": "0.1.0",
        "profileId": "exactmap",
        "profileVersion": "exactmap.v1",
    }
    assert value["model"]["revision"] == "b968826d9c46dd6066d109eabc6255188de91218"
    assert value["model"]["weightsSha256"] == MODEL_WEIGHT_DIGEST
    assert value["tokenizer"] == {
        "id": "Qwen/Qwen3-8B",
        "revision": TOKENIZER_REVISION,
        "chatTemplate": "qwen3",
    }
    assert value["configuration"]["verification"]["status"] == "passed"
    assert value["qualificationEligible"] is True


@pytest.mark.parametrize(
    ("engine", "expected_status"),
    (
        (UnobservedEngine(), "not-observed"),
        (PartialObservationEngine(), "not-observed"),
        (WrongGpuEngine(), "failed"),
    ),
)
def test_missing_or_wrong_hardware_observation_is_ineligible(
    engine: FakeEngine,
    expected_status: str,
) -> None:
    client = TestClient(create_app(engine, sealed_config()))

    value = client.get("/server_info").json()

    assert value["configuration"]["verification"]["status"] == expected_status
    assert value["qualificationEligible"] is False


def test_bootstrap_oracle_cannot_become_qualification_eligible() -> None:
    config = sealed_config().model_copy(update={"kernel_family": "bootstrap-transformers"})
    client = TestClient(create_app(BootstrapFamilyEngine(), config))

    value = client.get("/server_info").json()

    assert value["configuration"]["verification"]["status"] == "passed"
    assert value["qualificationEligible"] is False


def test_server_lifespan_closes_the_runtime_owner() -> None:
    engine = ClosableEngine()

    with TestClient(create_app(engine, EngineConfig())) as client:
        assert client.get("/ready").status_code == 200

    assert engine.closed is True


def test_streaming_chat_has_usage_and_done_marker() -> None:
    client = TestClient(create_app(FakeEngine(), sealed_config()))
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "Qwen/Qwen3-8B",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 2,
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    ) as response:
        response.raise_for_status()
        lines = [line for line in response.iter_lines() if line]

    assert lines[-1] == "data: [DONE]"
    payloads = [json.loads(line.removeprefix("data: ")) for line in lines[:-1]]
    usage = [item["usage"] for item in payloads if "usage" in item]
    assert usage == [{"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}]


@pytest.mark.parametrize(
    ("endpoint", "payload", "expected_text"),
    (
        (
            "/v1/completions",
            {"prompt": "Say hello"},
            "hello world",
        ),
        (
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "Say hello"}]},
            "hello world",
        ),
    ),
)
def test_nonstream_generation_has_exact_usage(
    endpoint: str,
    payload: dict[str, object],
    expected_text: str,
) -> None:
    client = TestClient(create_app(FakeEngine(), sealed_config()))
    response = client.post(
        endpoint,
        json={
            "model": "Qwen/Qwen3-8B",
            **payload,
            "max_tokens": 2,
            "stream": False,
        },
    )
    response.raise_for_status()
    value = response.json()

    choice = value["choices"][0]
    text = choice["message"]["content"] if "message" in choice else choice["text"]
    assert text == expected_text
    assert choice["finish_reason"] == "length"
    assert value["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 2,
        "total_tokens": 9,
    }


def test_request_contract_refuses_unknown_fields() -> None:
    client = TestClient(create_app(FakeEngine(), sealed_config()))
    response = client.post(
        "/v1/completions",
        json={
            "model": "Qwen/Qwen3-8B",
            "prompt": "hello",
            "max_tokens": 2,
            "unsupported": True,
        },
    )
    assert response.status_code == 422


def test_python_sources_do_not_import_competing_serving_engines() -> None:
    root = Path(__file__).parents[1]
    source_paths = [
        *sorted((root / "exactmap").rglob("*.py")),
        root / "build_manifest.py",
        root / "kernel_smoke.py",
        root / "modal_app.py",
        root / "serve.py",
    ]
    forbidden = {"vllm", "sglang", "tensorrt_llm"}
    for path in source_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            alias.name.split(".", maxsplit=1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".", maxsplit=1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert imported.isdisjoint(forbidden), path


def test_build_manifest_is_deterministic_and_create_only(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    artifact = tmp_path / "runtime.bin"
    artifact.write_bytes(b"exactmap")
    runtime_config = tmp_path / "runtime.json"
    runtime_config.write_text(
        json.dumps(EngineConfig().piq_configuration()),
        encoding="utf-8",
    )
    output = tmp_path / "manifest.json"
    command = [
        sys.executable,
        str(root / "build_manifest.py"),
        "--output",
        str(output),
        "--artifact-root",
        str(tmp_path),
        "--artifact",
        artifact.name,
        "--runtime-config",
        str(runtime_config),
        "--exactmap-revision",
        "1" * 40,
        "--vibesys-revision",
        "2" * 40,
        "--model-weight-digest",
        MODEL_WEIGHT_DIGEST,
        "--tokenizer-revision",
        TOKENIZER_REVISION,
        "--tuning-corpus-sha256",
        "sha256:" + ("5" * 64),
        "--search-recipe-sha256",
        "sha256:" + ("6" * 64),
        "--search-objective",
        "maximize aggregate output tokens per second",
        "--search-budget",
        "local-test",
        "--search-seed",
        "17",
        "--builder-image-digest",
        "sha256:" + ("7" * 64),
        "--compiler-id",
        "nvcc-test",
        "--cuda-version",
        "12.8",
        "--sbom-locator",
        "file://sbom.spdx.json",
    ]
    subprocess.run(command, check=True)
    first = output.read_bytes()
    value = json.loads(first)
    assert value["engineBuildSha256"].startswith("sha256:")
    assert value["engineBuildSha256"] != f"sha256:{hashlib.sha256(first).hexdigest()}"
    assert value["search"]["sealedEvaluationCohortUsed"] is False

    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(command, check=True)

    output.unlink()
    subprocess.run(command, check=True)
    assert output.read_bytes() == first


def test_build_manifest_rejects_symlinked_artifacts(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    artifact = tmp_path / "runtime.bin"
    artifact.write_bytes(b"exactmap")
    symlink = tmp_path / "runtime-link.bin"
    symlink.symlink_to(artifact)
    runtime_config = tmp_path / "runtime.json"
    runtime_config.write_text(
        json.dumps(EngineConfig().piq_configuration()),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(root / "build_manifest.py"),
        "--output",
        str(tmp_path / "manifest.json"),
        "--artifact-root",
        str(tmp_path),
        "--artifact",
        symlink.name,
        "--runtime-config",
        str(runtime_config),
        "--exactmap-revision",
        "1" * 40,
        "--vibesys-revision",
        "2" * 40,
        "--model-weight-digest",
        MODEL_WEIGHT_DIGEST,
        "--tokenizer-revision",
        TOKENIZER_REVISION,
        "--tuning-corpus-sha256",
        "sha256:" + ("5" * 64),
        "--search-recipe-sha256",
        "sha256:" + ("6" * 64),
        "--search-objective",
        "maximize aggregate output tokens per second",
        "--search-budget",
        "local-test",
        "--search-seed",
        "17",
        "--builder-image-digest",
        "sha256:" + ("7" * 64),
        "--compiler-id",
        "nvcc-test",
        "--cuda-version",
        "12.8",
        "--sbom-locator",
        "file://sbom.spdx.json",
    ]

    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert "must not traverse a symlink" in completed.stderr


def test_build_manifest_requires_complete_exactmap_profile(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    artifact = tmp_path / "runtime.bin"
    artifact.write_bytes(b"exactmap")
    runtime_config = EngineConfig().piq_configuration()
    runtime_config.pop("kv_block_size")
    runtime_config["unknown"] = True
    runtime_config_path = tmp_path / "runtime.json"
    runtime_config_path.write_text(json.dumps(runtime_config), encoding="utf-8")

    command = [
        sys.executable,
        str(root / "build_manifest.py"),
        "--output",
        str(tmp_path / "manifest.json"),
        "--artifact-root",
        str(tmp_path),
        "--artifact",
        artifact.name,
        "--runtime-config",
        str(runtime_config_path),
        "--exactmap-revision",
        "1" * 40,
        "--vibesys-revision",
        "2" * 40,
        "--model-weight-digest",
        MODEL_WEIGHT_DIGEST,
        "--tokenizer-revision",
        TOKENIZER_REVISION,
        "--tuning-corpus-sha256",
        "sha256:" + ("5" * 64),
        "--search-recipe-sha256",
        "sha256:" + ("6" * 64),
        "--search-objective",
        "maximize aggregate output tokens per second",
        "--search-budget",
        "local-test",
        "--search-seed",
        "17",
        "--builder-image-digest",
        "sha256:" + ("7" * 64),
        "--compiler-id",
        "nvcc-test",
        "--cuda-version",
        "12.8",
        "--sbom-locator",
        "file://sbom.spdx.json",
    ]

    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert "missing=['kv_block_size']" in completed.stderr
    assert "unknown=['unknown']" in completed.stderr
