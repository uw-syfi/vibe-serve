from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from exactmap.config import (
    MODEL_REVISION,
    MODEL_WEIGHT_DIGEST,
    TOKENIZER_REVISION,
    EngineConfig,
)

SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
REVISION = re.compile(r"[0-9a-f]{40}")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def load_runtime_configuration(path: Path) -> dict[str, object]:
    value = load_json_object(path, "runtime config")
    expected_keys = set(EngineConfig().piq_configuration())
    actual_keys = set(value)
    missing = sorted(expected_keys - actual_keys)
    unknown = sorted(actual_keys - expected_keys)
    if missing or unknown:
        raise ValueError(
            "runtime config must contain exactly the exactmap.v1 keys; "
            f"missing={missing}, unknown={unknown}"
        )
    validated = EngineConfig.model_validate(value).piq_configuration()
    if value != validated:
        raise ValueError("runtime config does not use canonical exactmap.v1 values")
    return validated


def artifact_inventory(root: Path, paths: list[Path]) -> list[dict[str, object]]:
    resolved_root = root.resolve(strict=True)
    inventory = []
    seen: set[str] = set()
    for candidate in sorted(paths, key=lambda item: item.as_posix()):
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            raise ValueError(f"artifact path must stay relative to artifact root: {candidate}")
        unresolved = resolved_root / candidate
        cursor = resolved_root
        for part in candidate.parts:
            if part in {"", "."}:
                continue
            cursor /= part
            if cursor.is_symlink():
                raise ValueError(f"artifact must not traverse a symlink: {candidate}")
        path = unresolved.resolve(strict=True)
        try:
            relative = path.relative_to(resolved_root).as_posix()
        except ValueError as exc:
            raise ValueError(f"artifact escapes artifact root: {candidate}") from exc
        if relative in seen:
            raise ValueError(f"duplicate artifact path: {relative}")
        if not path.is_file():
            raise ValueError(f"artifact must be a regular non-symlink file: {relative}")
        seen.add(relative)
        body = path.read_bytes()
        inventory.append(
            {
                "path": relative,
                "sizeBytes": len(body),
                "sha256": sha256_bytes(body),
            }
        )
    if not inventory:
        raise ValueError("at least one artifact file is required")
    return inventory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a canonical ExactMap manifest.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifact-root", default=Path.cwd(), type=Path)
    parser.add_argument("--artifact", action="append", required=True, type=Path)
    parser.add_argument("--runtime-config", required=True, type=Path)
    parser.add_argument("--exactmap-revision", required=True)
    parser.add_argument("--vibesys-revision", required=True)
    parser.add_argument("--model-weight-digest", required=True)
    parser.add_argument("--tokenizer-revision", required=True)
    parser.add_argument("--tuning-corpus-sha256", required=True)
    parser.add_argument("--search-recipe-sha256", required=True)
    parser.add_argument("--search-objective", required=True)
    parser.add_argument("--search-budget", required=True)
    parser.add_argument("--search-seed", required=True, type=int)
    parser.add_argument("--builder-image-digest", required=True)
    parser.add_argument("--compiler-id", required=True)
    parser.add_argument("--cuda-version", required=True)
    parser.add_argument("--build-flag", action="append", default=[])
    parser.add_argument("--sbom-locator", required=True)
    return parser.parse_args()


def require_digest(value: str, label: str) -> str:
    if SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def require_revision(value: str, label: str) -> str:
    if REVISION.fullmatch(value) is None:
        raise ValueError(f"{label} must be a 40-character lowercase git revision")
    return value


def build_manifest(args: argparse.Namespace) -> dict[str, object]:
    runtime_config = load_runtime_configuration(args.runtime_config)
    model_weight_digest = require_digest(args.model_weight_digest, "model weight digest")
    if model_weight_digest != MODEL_WEIGHT_DIGEST:
        raise ValueError("model weight digest does not match the frozen Qwen3-8B identity")
    tokenizer_revision = require_revision(args.tokenizer_revision, "tokenizer revision")
    if tokenizer_revision != TOKENIZER_REVISION:
        raise ValueError("tokenizer revision does not match the frozen Qwen3-8B identity")
    body = {
        "schemaVersion": "exactmap.engine-build-manifest.v1",
        "runtime": {
            "engine": "custom",
            "product": "ExactMap",
            "version": "0.1.0",
            "profileId": "exactmap",
            "profileVersion": "exactmap.v1",
        },
        "factory": {
            "exactmapRevision": require_revision(args.exactmap_revision, "ExactMap revision"),
            "vibesysRevision": require_revision(args.vibesys_revision, "VibeSys revision"),
            "builderImageDigest": require_digest(args.builder_image_digest, "builder image digest"),
            "compilerId": args.compiler_id,
            "cudaVersion": args.cuda_version,
            "buildFlags": sorted(set(args.build_flag)),
        },
        "model": {
            "id": "Qwen/Qwen3-8B",
            "revision": MODEL_REVISION,
            "weightDigest": model_weight_digest,
            "tokenizerRevision": tokenizer_revision,
        },
        "hardware": {
            "accelerator": "NVIDIA L40S",
            "acceleratorCount": 1,
            "computeCapability": "8.9",
        },
        "search": {
            "tuningCorpusSha256": require_digest(args.tuning_corpus_sha256, "tuning corpus digest"),
            "recipeSha256": require_digest(args.search_recipe_sha256, "search recipe digest"),
            "objective": args.search_objective,
            "budget": args.search_budget,
            "seed": args.search_seed,
            "sealedEvaluationCohortUsed": False,
        },
        "runtimeConfiguration": runtime_config,
        "artifacts": artifact_inventory(args.artifact_root, args.artifact),
        "sbom": {"locator": args.sbom_locator},
    }
    return {
        **body,
        "engineBuildSha256": sha256_bytes(canonical_json(body)),
    }


def write_create_only(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json(value))
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def main() -> None:
    args = parse_args()
    write_create_only(args.output, build_manifest(args))


if __name__ == "__main__":
    main()
