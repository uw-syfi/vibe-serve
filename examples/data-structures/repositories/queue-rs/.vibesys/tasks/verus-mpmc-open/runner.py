from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

TASK_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_ROOT.parents[2]
CANDIDATE_ROOT = PROJECT_ROOT / "verus-mpmc"
CANDIDATE_MANIFEST = CANDIDATE_ROOT / "Cargo.toml"
TARGET_ROOT = Path(
    os.environ.get(
        "VIBESYS_VERUS_TASK_TARGET",
        str(PROJECT_ROOT / "target" / "verus-mpmc-task"),
    )
)
METRIC_PREFIX = "total_ops_per_sec="
FORBIDDEN_PROOF_BYPASSES = (
    "assume(",
    "admit(",
    "axiom",
    "external_body",
    "external_fn_specification",
    "verifier::external",
)


def _run(command: list[str], *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    TARGET_ROOT.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["CARGO_TARGET_DIR"] = str(TARGET_ROOT / "candidate")
    try:
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            check=True,
            capture_output=capture_output,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"required command was not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        if capture_output:
            detail = (exc.stderr or exc.stdout or "command failed").strip()
            raise RuntimeError(detail) from exc
        raise RuntimeError(f"command failed with exit code {exc.returncode}") from exc


def _verify_candidate() -> None:
    if not CANDIDATE_MANIFEST.is_file():
        raise RuntimeError(f"candidate manifest not found: {CANDIDATE_MANIFEST}")
    manifest = tomllib.loads(CANDIDATE_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("package", {}).get("metadata", {}).get("verus", {}).get("verify") is not True:
        raise RuntimeError("candidate must keep package.metadata.verus.verify = true")
    for source in CANDIDATE_ROOT.rglob("*.rs"):
        contents = source.read_text(encoding="utf-8")
        bypass = next((token for token in FORBIDDEN_PROOF_BYPASSES if token in contents), None)
        if bypass is not None:
            raise RuntimeError(f"forbidden proof bypass {bypass!r} in {source}")
    _run(
        [
            "cargo",
            "check",
            "--manifest-path",
            str(CANDIDATE_MANIFEST),
            "--locked",
        ]
    )
    if shutil.which("cargo-verus") is None:
        raise RuntimeError(
            "cargo-verus was not found on PATH; install the Verus release that matches vstd"
        )
    _run(
        [
            "cargo",
            "verus",
            "verify",
            "--manifest-path",
            str(CANDIDATE_MANIFEST),
            "--locked",
        ]
    )


def _run_task_crate(
    crate: str,
    arguments: list[str],
    *,
    release: bool = False,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    TARGET_ROOT.mkdir(parents=True, exist_ok=True)
    source_root = TASK_ROOT / crate
    with tempfile.TemporaryDirectory(prefix=f"{crate}-", dir=TARGET_ROOT) as temporary:
        staged_root = Path(temporary)
        (staged_root / "src").mkdir()
        manifest_template = (source_root / "Cargo.toml.in").read_text(encoding="utf-8")
        candidate_path = json.dumps(str(CANDIDATE_ROOT))
        (staged_root / "Cargo.toml").write_text(
            manifest_template.replace("__CANDIDATE_PATH__", candidate_path),
            encoding="utf-8",
        )
        shutil.copy2(source_root / "src" / "main.rs", staged_root / "src" / "main.rs")
        environment = os.environ.copy()
        environment["CARGO_TARGET_DIR"] = str(TARGET_ROOT / crate)
        cargo_arguments = ["cargo", "run", "--quiet"]
        if release:
            cargo_arguments.append("--release")
        cargo_arguments.extend(
            [
                "--manifest-path",
                str(staged_root / "Cargo.toml"),
                "--",
                *arguments,
            ]
        )
        try:
            return subprocess.run(
                cargo_arguments,
                cwd=PROJECT_ROOT,
                env=environment,
                check=True,
                capture_output=capture_output,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("required command was not found: cargo") from exc
        except subprocess.CalledProcessError as exc:
            if capture_output:
                detail = (exc.stderr or exc.stdout or "harness failed").strip()
                raise RuntimeError(detail) from exc
            raise RuntimeError(f"harness failed with exit code {exc.returncode}") from exc


def _check() -> None:
    _verify_candidate()
    _run_task_crate("accuracy", [])


def _benchmark(args: argparse.Namespace) -> None:
    if args.duration_seconds <= 0:
        raise RuntimeError("--duration-seconds must be greater than zero")
    for name in ("capacity", "producers", "consumers"):
        if getattr(args, name) <= 0:
            raise RuntimeError(f"--{name} must be greater than zero")

    duration_ms = max(1, round(args.duration_seconds * 1000))
    completed = _run_task_crate(
        "benchmark",
        [
            str(duration_ms),
            str(args.capacity),
            str(args.producers),
            str(args.consumers),
        ],
        release=True,
        capture_output=True,
    )
    metric_line = next(
        (line for line in completed.stdout.splitlines() if line.startswith(METRIC_PREFIX)),
        None,
    )
    if metric_line is None:
        raise RuntimeError("benchmark harness did not report total_ops_per_sec")
    try:
        throughput = float(metric_line.removeprefix(METRIC_PREFIX))
    except ValueError as exc:
        raise RuntimeError("benchmark harness reported an invalid throughput") from exc

    result = {
        "total_ops_per_sec": throughput,
        "duration_seconds": args.duration_seconds,
        "capacity": args.capacity,
        "producers": args.producers,
        "consumers": args.consumers,
    }
    if args.output_json is not None:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pure-Rust Verus MPMC task runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check")
    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--duration-seconds", type=float, default=1.0)
    benchmark.add_argument("--capacity", type=int, default=1024)
    benchmark.add_argument("--producers", type=int, default=4)
    benchmark.add_argument("--consumers", type=int, default=4)
    benchmark.add_argument("--output-json", type=str, default=None)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    if args.command == "check":
        _check()
    else:
        _benchmark(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL - {exc}")
        raise SystemExit(1) from None
