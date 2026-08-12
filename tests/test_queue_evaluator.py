from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tomllib
from collections.abc import Iterator  # noqa: TC003  # tracked: #288
from pathlib import Path

import pytest

LINEARIZABLE_QUEUE_INPUTS = {
    "queue-spsc": "spsc",
    "queue-mpsc": "mpsc",
    "queue-mpmc": "mpmc",
}

LINEARIZABLE_ACCURACY_SETTINGS = {
    "queue-spsc": ("32", "100"),
    "queue-mpsc": ("24", "50"),
    "queue-mpmc": ("24", "100"),
}


@pytest.fixture(scope="session")
def compiled_queue_candidate(tmp_path_factory) -> Path:  # noqa: ANN001  # tracked: #288
    """Build the shared Rust starter once for materialized-input tests."""
    if shutil.which("cargo") is None:
        pytest.skip("Rust is required by the trusted queue evaluator")

    project_root = Path(__file__).parents[1]
    starter = project_root / "examples" / "starters" / "queue-rs"
    build_dir = tmp_path_factory.mktemp("queue-rs-build") / "starter"
    shutil.copytree(starter, build_dir)
    subprocess.run(["make"], cwd=build_dir, check=True)  # noqa: S607  # tracked: #288

    candidate = build_dir / "queue-candidate.so"
    assert candidate.is_file()
    return candidate


@pytest.fixture(scope="session")
def queue_native_runner(tmp_path_factory) -> Iterator[Path]:  # noqa: ANN001  # tracked: #288
    """Build the trusted evaluator runner once and reuse it across subprocesses."""
    if shutil.which("cargo") is None:
        pytest.skip("Rust is required by the trusted queue evaluator")

    project_root = Path(__file__).parents[1]
    source = project_root / "examples" / "evaluators" / "queue" / "native_runner"
    target_dir = tmp_path_factory.mktemp("queue-native-runner") / "target"
    subprocess.run(  # noqa: S603  # tracked: #288
        [  # noqa: S607  # tracked: #288
            "cargo",
            "build",
            "--quiet",
            "--release",
            "--locked",
            "--manifest-path",
            str(source / "Cargo.toml"),
            "--target-dir",
            str(target_dir),
        ],
        cwd=source,
        check=True,
    )
    runner = target_dir / "release" / "vibesys-queue-native-runner"
    assert runner.is_file()

    environment = pytest.MonkeyPatch()
    environment.setenv("VIBESYS_QUEUE_NATIVE_RUNNER", str(runner))
    try:
        yield runner
    finally:
        environment.undo()


def _copy_input_bundle(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".venv", "queue-candidate.so", "target"),
    )


def _materialize_linearizable_input(
    project_root: Path,
    input_name: str,
    workspace: Path,
) -> Path:
    from vibesys.input_manifest import load_input_bundle  # noqa: PLC0415  # tracked: #288

    input_dir = project_root / "examples" / "data-structures" / input_name
    bundle = load_input_bundle(input_dir)
    assert bundle.workspace_seed_path is not None
    assert bundle.evaluator_path is not None
    _copy_input_bundle(bundle.workspace_seed_path, workspace)
    _copy_input_bundle(input_dir, workspace)
    _copy_input_bundle(
        bundle.evaluator_path,
        workspace / "_evaluator" / bundle.evaluator_path.name,
    )
    return input_dir


def test_linearizable_queue_manifests_invoke_go_evaluator_directly():  # noqa: ANN201  # tracked: #288
    root = Path(__file__).parents[1] / "examples" / "data-structures"

    for input_name, scenario in LINEARIZABLE_QUEUE_INPUTS.items():
        manifest = tomllib.loads((root / input_name / "vibesys.input.toml").read_text())
        operations, trials = LINEARIZABLE_ACCURACY_SETTINGS[input_name]
        expected_suffixes = {
            "accuracy": [
                "run",
                ".",
                "check",
                "--workspace",
                "../..",
                "--scenario",
                scenario,
                "--operations",
                operations,
                "--trials",
                trials,
            ],
            "benchmark": [
                "run",
                ".",
                "benchmark",
                "--workspace",
                "../..",
                "--scenario",
                scenario,
                "--repetitions",
                "3",
            ],
        }
        assert manifest["agent"] == {"domain": "generic"}
        assert manifest["evaluator"] == {"source": "../../evaluators/queue"}
        for section, expected_suffix in expected_suffixes.items():
            command = manifest[section]["command"]
            assert command[:3] == ["go", "-C", "_evaluator/queue"]
            assert command[3:] == expected_suffix
        assert manifest["benchmark"]["result"] == {
            "json_argument": "--output-json",
            "metric": "total_ops_per_sec",
        }

    evaluator = root.parents[0] / "evaluators" / "queue"
    assert (evaluator / "DESIGN.md").exists()
    assert (evaluator / "CANDIDATE_CONTRACT.md").exists()
    assert (evaluator / "include" / "vibesys_queue_abi.h").exists()
    assert not (evaluator / "QUEUE_PROTOCOL.md").exists()
    old_core = root.parents[0] / "libs" / "queue-input-core"
    assert not (old_core / "pyproject.toml").exists()
    assert not any(old_core.glob("src/queue_input_core/*.py"))


def test_linearizable_queue_inputs_use_shared_editable_rust_starter():  # noqa: ANN201  # tracked: #288
    from vibesys.input_manifest import load_input_bundle  # noqa: PLC0415  # tracked: #288

    project_root = Path(__file__).parents[1]
    root = project_root / "examples" / "data-structures"
    starter = project_root / "examples" / "starters" / "queue-rs"
    starter_files = [".gitignore", "Cargo.toml", "Cargo.lock", "Makefile", "src/lib.rs"]

    for relative in starter_files:
        assert (starter / relative).is_file()

    for input_name in LINEARIZABLE_QUEUE_INPUTS:
        input_dir = root / input_name
        bundle = load_input_bundle(input_dir)
        assert bundle.workspace_seed_path == starter.resolve()
        assert (
            bundle.evaluator_path == (project_root / "examples" / "evaluators" / "queue").resolve()
        )
        assert not (input_dir / "baseline").exists()
        assert not (input_dir / "reference" / "reference.py").exists()
        assert not (input_dir / "pyproject.toml").exists()
        for relative in starter_files:
            assert not (input_dir / relative).exists()


@pytest.mark.usefixtures("queue_native_runner")
def test_spsc_rigtorp_baseline_builds_and_passes_accuracy(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    if shutil.which("go") is None or shutil.which("c++") is None:
        pytest.skip("Go and a C++ compiler are required by the SPSC baseline")

    project_root = Path(__file__).parents[1]
    baseline_source = project_root / "examples" / "baselines" / "queue-spsc-rigtorp"
    baseline = tmp_path / "queue-spsc-rigtorp"
    shutil.copytree(baseline_source, baseline)
    evaluator = project_root / "examples" / "evaluators" / "queue"
    abi_header = evaluator / "include" / "vibesys_queue_abi.h"

    subprocess.run(  # noqa: S603  # tracked: #288
        ["make", "clean", "all", f"ABI_HEADER={abi_header}"],  # noqa: S607  # tracked: #288
        cwd=baseline,
        check=True,
    )
    completed = subprocess.run(  # noqa: S603  # tracked: #288
        [  # noqa: S607  # tracked: #288
            "go",
            "-C",
            str(evaluator),
            "run",
            ".",
            "check",
            "--workspace",
            str(baseline),
            "--scenario",
            "spsc",
            "--capacity",
            "4",
            "--value-size",
            "64",
            "--operations",
            "12",
            "--trials",
            "4",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "PASS - spsc linearizable" in completed.stdout


def test_spsc_rigtorp_baseline_uses_pinned_upstream_header():  # noqa: ANN201  # tracked: #288
    project_root = Path(__file__).parents[1]
    baseline = project_root / "examples" / "baselines" / "queue-spsc-rigtorp"
    upstream_header = baseline / "include" / "rigtorp" / "SPSCQueue.h"
    adapter = (baseline / "spsc_queue.cpp").read_text()

    assert hashlib.sha256(upstream_header.read_bytes()).hexdigest() == (
        "1e631ec9e8ba4955da5cac116620815055f7da0ea936bfdb3036c4e87bc8a6e8"
    )
    assert '#include "rigtorp/SPSCQueue.h"' in adapter
    assert "rigtorp::SPSCQueue<QueueEntry> entries" in adapter


@pytest.mark.usefixtures("queue_native_runner")
def test_mpmc_locked_ring_baseline_builds_and_passes_accuracy(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    if shutil.which("go") is None or shutil.which("cc") is None:
        pytest.skip("Go and a C compiler are required by the MPMC baseline")

    project_root = Path(__file__).parents[1]
    baseline_source = project_root / "examples" / "baselines" / "queue-mpmc-locked-ring"
    baseline = tmp_path / "queue-mpmc-locked-ring"
    shutil.copytree(baseline_source, baseline)
    evaluator = project_root / "examples" / "evaluators" / "queue"
    abi_header = evaluator / "include" / "vibesys_queue_abi.h"

    subprocess.run(  # noqa: S603  # tracked: #288
        ["make", "clean", "all", f"ABI_HEADER={abi_header}"],  # noqa: S607  # tracked: #288
        cwd=baseline,
        check=True,
    )
    completed = subprocess.run(  # noqa: S603  # tracked: #288
        [  # noqa: S607  # tracked: #288
            "go",
            "-C",
            str(evaluator),
            "run",
            ".",
            "check",
            "--workspace",
            str(baseline),
            "--scenario",
            "mpmc",
            "--capacity",
            "17",
            "--value-size",
            "64",
            "--operations",
            "16",
            "--trials",
            "12",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "PASS - mpmc reservation-aware bounded FIFO" in completed.stdout


def test_mpmc_locked_ring_baseline_has_atomic_publication_region():  # noqa: ANN201  # tracked: #288
    project_root = Path(__file__).parents[1]
    baseline = project_root / "examples" / "baselines" / "queue-mpmc-locked-ring"
    adapter = (baseline / "locked_ring.c").read_text()

    enqueue = adapter[adapter.index("vsq_status vsq_try_enqueue") :]
    enqueue = enqueue[: enqueue.index("vsq_status vsq_try_dequeue")]
    dequeue = adapter[adapter.index("vsq_status vsq_try_dequeue") :]
    assert "pthread_mutex_lock(&queue->lock)" in enqueue
    assert "memcpy(" in enqueue
    assert enqueue.index("pthread_mutex_lock") < enqueue.index("memcpy(")
    assert "pthread_mutex_lock(&queue->lock)" in dequeue
    assert "memcpy(" in dequeue
    assert dequeue.index("pthread_mutex_lock") < dequeue.index("memcpy(")


@pytest.mark.parametrize(("input_name", "scenario"), LINEARIZABLE_QUEUE_INPUTS.items())
@pytest.mark.usefixtures("queue_native_runner")
def test_materialized_rust_starter_passes_accuracy(  # noqa: ANN201  # tracked: #288
    tmp_path,  # noqa: ANN001  # tracked: #288
    input_name,  # noqa: ANN001  # tracked: #288
    scenario,  # noqa: ANN001  # tracked: #288
    compiled_queue_candidate,  # noqa: ANN001  # tracked: #288
):
    if shutil.which("go") is None or shutil.which("cargo") is None:
        pytest.skip("Go and Rust are required by the trusted queue evaluator")

    project_root = Path(__file__).parents[1]
    workspace = tmp_path / "workspace"
    _materialize_linearizable_input(project_root, input_name, workspace)

    shutil.copy2(compiled_queue_candidate, workspace / "queue-candidate.so")
    assert (workspace / "queue-candidate.so").is_file()

    manifest = tomllib.loads((workspace / "vibesys.input.toml").read_text())
    accuracy = [
        *manifest["accuracy"]["command"],
        "--capacity",
        "4",
        "--value-size",
        "64",
        "--operations",
        "12",
        "--trials",
        "1",
    ]
    completed = subprocess.run(  # noqa: S603  # tracked: #288
        accuracy,
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    expected = ["spsc", "mpsc", "mpmc"] if scenario == "all" else [scenario]
    for checked_scenario in expected:
        contract = (
            "reservation-aware bounded FIFO"
            if checked_scenario == "mpmc"
            else "linearizable bounded FIFO"
        )
        assert f"PASS - {checked_scenario} {contract}" in completed.stdout


@pytest.mark.usefixtures("queue_native_runner")
def test_materialized_manifest_commands_run_go_evaluator_directly(tmp_path):  # noqa: ANN001, ANN201  # tracked: #288
    if shutil.which("go") is None or shutil.which("cargo") is None:
        pytest.skip("Go and Rust are required by the trusted queue evaluator")

    project_root = Path(__file__).parents[1]
    workspace = tmp_path / "workspace"
    input_dir = _materialize_linearizable_input(
        project_root,
        "queue-spsc",
        workspace,
    )
    assert (workspace / "_evaluator" / "queue" / "DESIGN.md").is_file()
    subprocess.run(["make"], cwd=workspace, check=True)  # noqa: S607  # tracked: #288
    manifest = tomllib.loads((input_dir / "vibesys.input.toml").read_text())

    accuracy = [
        *manifest["accuracy"]["command"],
        "--capacity",
        "4",
        "--operations",
        "12",
        "--trials",
        "1",
    ]
    subprocess.run(accuracy, cwd=workspace, check=True)  # noqa: S603  # tracked: #288

    output = workspace / "results.json"
    benchmark = [
        *manifest["benchmark"]["command"],
        "--capacity",
        "4",
        "--duration",
        "20ms",
        "--warmup",
        "0s",
        "--output-json",
        str(output),
    ]
    subprocess.run(benchmark, cwd=workspace, check=True)  # noqa: S603  # tracked: #288
    results = json.loads(output.read_text())
    assert [result["scenario"] for result in results] == ["spsc"]
    assert all(result["repetitions"] == 3 for result in results)
    assert all(len(result["total_ops_per_sec_samples"]) == 3 for result in results)


@pytest.mark.usefixtures("queue_native_runner")
def test_queue_evaluator_rejects_adversarial_histories():  # noqa: ANN201  # tracked: #288
    if shutil.which("go") is None or shutil.which("cargo") is None:
        pytest.skip("Go and Rust are required by the trusted queue evaluator")

    evaluator = Path(__file__).parents[1] / "examples" / "evaluators" / "queue"
    subprocess.run(["go", "test", "./..."], cwd=evaluator, check=True)  # noqa: S607  # tracked: #288
