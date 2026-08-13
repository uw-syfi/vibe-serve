import json
import tomllib
from itertools import pairwise
from pathlib import Path

import pytest

from vibesys.evaluators import PROJECT_ROOT_TOKEN
from vibesys.input_manifest import InputBundle, load_input_bundle, load_project_task
from vs_project import Project, ProjectLayoutError

PROJECT_ROOT = Path(__file__).parents[1]
MICROSERVICE_ROOT = PROJECT_ROOT / "examples" / "microservices"
DEATHSTAR_ROOT = MICROSERVICE_ROOT / "repositories" / "deathstarbench"
try:
    DEATHSTAR_LAYOUT = Project.open(DEATHSTAR_ROOT)
    DEATHSTAR_LAYOUT.discover_tasks()
except ProjectLayoutError:
    pytest.skip("DeathStarBench repository example is not initialized", allow_module_level=True)
DEATHSTAR_TASKS = {task.name.value: task for task in DEATHSTAR_LAYOUT.discover_tasks()}
LEGACY_SCENARIOS = (MICROSERVICE_ROOT / "train-ticket",)
HOTEL_TEMP_ROOT = Path("/") / "tmp" / "vibesys-hotel-reservation" / "otel"


def _deathstar_bundle(task_name: str) -> InputBundle:
    return load_project_task(DEATHSTAR_LAYOUT, DEATHSTAR_TASKS[task_name])


def _adjacent_pairs(command: tuple[str, ...]) -> set[tuple[str, str]]:
    return set(pairwise(command))


def test_legacy_microservice_scenario_uses_source_evaluator() -> None:
    bundle = load_input_bundle(MICROSERVICE_ROOT / "train-ticket")

    assert bundle.evaluator_path == PROJECT_ROOT / "resources" / "evaluators" / "microservice"
    assert bundle.benchmark_command[:5] == (
        "go",
        "-C",
        "_evaluator/microservice",
        "run",
        "./cmd/servicebench",
    )
    assert bundle.benchmark_result is not None
    assert bundle.benchmark_result.json_argument == "--output-json"
    assert bundle.benchmark_result.metric == "primary_value"
    assert ("--seed", "random") in _adjacent_pairs(bundle.benchmark_command)
    assert ("--fixture-seed", "random") in _adjacent_pairs(bundle.benchmark_command)


@pytest.mark.parametrize(
    "task_name",
    ["hotel-reservation", "social-network-read-timeline"],
)
def test_deathstar_tasks_use_locked_packaged_evaluator(task_name: str) -> None:
    bundle = _deathstar_bundle(task_name)

    assert bundle.root == DEATHSTAR_ROOT.resolve()
    assert bundle.task_name == task_name
    assert bundle.evaluator_path is None
    assert bundle.evaluator_package_digest is not None
    assert bundle.benchmark_command[:5] == (
        "go",
        "-C",
        str(PROJECT_ROOT / "resources" / "evaluators" / "microservice"),
        "run",
        "./cmd/servicebench",
    )
    assert bundle.benchmark_result is not None
    assert bundle.benchmark_result.json_argument == "--output-json"
    assert bundle.benchmark_result.metric == "primary_value"


def test_microservice_scenarios_are_discovered() -> None:
    assert set(DEATHSTAR_TASKS) == {
        "hotel-reservation",
        "social-network-read-timeline",
    }
    assert {path.name for path in LEGACY_SCENARIOS} == {"train-ticket"}


def test_train_ticket_accuracy_uses_source_evaluator() -> None:
    bundle = load_input_bundle(MICROSERVICE_ROOT / "train-ticket")

    assert bundle.accuracy_command[:5] == (
        "go",
        "-C",
        "_evaluator/microservice",
        "run",
        "./cmd/servicebench",
    )
    assert bundle.accuracy_command[5:7] == ("--mode", "accuracy")


def test_hotel_accuracy_and_benchmark_preserve_randomized_stateful_workload() -> None:
    bundle = _deathstar_bundle("hotel-reservation")
    accuracy_pairs = _adjacent_pairs(bundle.accuracy_command)
    benchmark_pairs = _adjacent_pairs(bundle.benchmark_command)
    assert bundle.accuracy_command[:5] == (
        "go",
        "-C",
        str(PROJECT_ROOT / "resources" / "evaluators" / "microservice"),
        "run",
        "./cmd/servicebench",
    )
    assert ("--mode", "accuracy") in accuracy_pairs
    assert ("--seed", "random") in accuracy_pairs
    assert ("--seed", "random") in benchmark_pairs
    assert ("--fixture-seed", "random") in benchmark_pairs
    assert (
        "--candidate-dir",
        f"{PROJECT_ROOT_TOKEN}/hotelReservation",
    ) in accuracy_pairs
    assert (
        "--workload",
        f"{PROJECT_ROOT_TOKEN}/.vibesys/tasks/hotel-reservation/benchmark/workload.toml",
    ) in benchmark_pairs
    assert (
        "--run-command-json",
        '["docker","compose","up","-d","--build"]',
    ) in accuracy_pairs
    assert (
        "--stop-command-json",
        '["docker","compose","stop","-t","10","frontend","geo","profile","rate",'
        '"recommendation","reservation","search","user"]',
    ) in accuracy_pairs
    assert (
        "--cleanup-command-json",
        '["docker","compose","down","-v","--remove-orphans"]',
    ) in accuracy_pairs
    assert ("--telemetry-output", str(HOTEL_TEMP_ROOT / "telemetry.json")) in (benchmark_pairs)
    assert (
        "--trace-graph-json",
        str(HOTEL_TEMP_ROOT / "trace-graph.json"),
    ) in benchmark_pairs
    assert ("--telemetry-timeout", "60") in benchmark_pairs
    assert "--telemetry-command-json" not in bundle.accuracy_command

    run_command = bundle.benchmark_command[bundle.benchmark_command.index("--run-command-json") + 1]
    run_argv = json.loads(run_command)
    assert run_argv[:2] == ["sh", "-c"]
    assert 'go -C "$1" run ./cmd/otelinject' in run_argv[2]
    assert run_argv[4] == str(PROJECT_ROOT / "resources" / "evaluators" / "microservice")
    assert run_argv[5] == f"{PROJECT_ROOT_TOKEN}/hotelReservation/docker-compose.yml"


@pytest.mark.parametrize(
    "scenario_path",
    [
        MICROSERVICE_ROOT / "train-ticket",
        DEATHSTAR_TASKS["hotel-reservation"].path,
        DEATHSTAR_TASKS["social-network-read-timeline"].path,
    ],
    ids=("train-ticket", "hotel-reservation", "social-network-read-timeline"),
)
def test_microservice_scenario_has_no_embedded_legacy_generator(
    scenario_path: Path,
) -> None:
    benchmark_dir = scenario_path / "benchmark"
    legacy_sources = sorted(
        path.relative_to(scenario_path)
        for path in benchmark_dir.iterdir()
        if path.name == "benchmark" or path.suffix in {".cpp", ".py"}
    )

    assert legacy_sources == []


def test_social_network_workload_uses_stateful_semantic_operation() -> None:
    workload_path = (
        DEATHSTAR_TASKS["social-network-read-timeline"].path / "benchmark" / "workload.toml"
    )
    with workload_path.open("rb") as file:
        workload = tomllib.load(file)

    operations = {operation["name"]: operation for operation in workload["operations"]}
    assert set(operations) == {
        "user_timeline_read",
        "home_timeline_read",
        "compose_user_timeline",
    }
    assert operations["compose_user_timeline"]["tags"] == [
        "write",
        "read-your-write",
    ]
    assert {
        capture["header"] for capture in operations["compose_user_timeline"]["capture_headers"]
    } == {
        "X-Compose-Thrift-Ms",
        "X-UserTimeline-Thrift-Ms",
        "X-HomeTimeline-Thrift-Ms",
    }
    assert workload["load"]["seed"] == 42
    assert workload["load"]["fixture_seed"] == 42
    assert workload["constraints"]["min_operations_per_type"] == 1


def test_hotel_workload_preserves_canonical_mix_and_stateful_gate() -> None:
    workload_path = DEATHSTAR_TASKS["hotel-reservation"].path / "benchmark" / "workload.toml"
    with workload_path.open("rb") as file:
        workload = tomllib.load(file)

    operations = {operation["name"]: operation for operation in workload["operations"]}
    assert {name: operation["weight"] for name, operation in operations.items()} == {
        "search_hotels": 600,
        "recommend_distance": 130,
        "recommend_rate": 130,
        "recommend_price": 130,
        "login_valid": 3,
        "login_invalid": 2,
        "reserve_capacity": 5,
    }
    assert operations["reserve_capacity"]["tags"] == ["write", "read-your-write"]
    assert workload["load"]["model"] == "closed_loop"
    assert workload["load"]["repetitions"] == 3
    assert workload["profiles"]["quick"]["repetitions"] == 1
    assert workload["constraints"] == {
        "min_success_rate": 1.0,
        "max_error_rate": 0.0,
        "min_operations_per_type": 1,
    }
