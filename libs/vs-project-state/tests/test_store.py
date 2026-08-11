from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from pydantic import ValidationError

from vs_loop_state import RoundRecord, parse_round_record, serialize_round_record
from vs_project_state import (
    PROJECT_SCHEMA_VERSION,
    ProjectManifest,
    ProjectStateError,
    ProjectStore,
    RunConfiguration,
    RunManifest,
    generate_run_id,
    serialize_round,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 8, 11, 12, 34, 56, tzinfo=UTC)
UNIQUE = UUID("12345678-1234-5678-1234-567812345678")


def _configuration() -> RunConfiguration:
    return RunConfiguration(
        model="gpt-5",
        outer_loop="agent",
        inner_loop="multi-agent",
        interface="inprocess",
        agent_backend="cli",
        cli_provider="codex",
        cli_timeout=1800,
        compute_backend="cpu",
        profiler="linux-cpu",
        max_rounds=10,
        max_retries_per_round=3,
        judge_every=3,
        official_eval_every=3,
        memory_layout="files",
        modality="text_generation",
        default_reasoning_effort="high",
        outer_model="gpt-5.6-sol",
        outer_reasoning_effort="xhigh",
        inner_model="gpt-5.6-luna",
        inner_reasoning_effort="medium",
        operator_constraints=("Do not change the ABI",),
    )


def _store(tmp_path: Path) -> ProjectStore:
    (tmp_path / "OBJECTIVE.md").write_text("Make it fast.\n", encoding="utf-8")
    store = ProjectStore(tmp_path)
    store.create_project("Queue SPSC", now=NOW)
    return store


def _run(store: ProjectStore, *, minute: int = 0) -> RunManifest:
    created_at = NOW + timedelta(minutes=minute)
    manifest = store.new_run_manifest(
        "Queue SPSC",
        branch=f"vibesys/queue-{minute}",
        vibesys_version="0.2.0",
        configuration=_configuration(),
        trusted_input_baseline="a" * 40,
        now=created_at,
        unique=UUID(int=minute + 1),
    )
    store.create_run(manifest)
    return manifest


def test_generate_run_id_is_sortable_safe_and_deterministic() -> None:
    run_id = generate_run_id("  Quéúe / SPSC?!  ", now=NOW, unique=UNIQUE)

    assert run_id == "20260811-123456-12345678-queue-spsc"
    assert "/" not in run_id


def test_generate_run_id_rejects_naive_time() -> None:
    naive = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)

    with pytest.raises(ProjectStateError, match="timezone"):
        generate_run_id("queue", now=naive, unique=UNIQUE)


def test_manifests_are_strict_versioned_contracts() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        ProjectManifest.model_validate(
            {
                "schema_version": "1",
                "project_id": "queue-abc",
                "created_at": NOW,
                "initial_input_fingerprint": "a" * 64,
            }
        )
    forbidden_value = ""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RunManifest(
            schema_version=PROJECT_SCHEMA_VERSION,
            run_id="run-1",
            project_id="queue-abc",
            display_name="Queue",
            created_at=NOW,
            input_fingerprint="a" * 64,
            trusted_input_baseline="b" * 40,
            branch="vibesys/run-1",
            vibesys_version="0.2.0",
            configuration=_configuration(),
            provider_token=forbidden_value,  # type: ignore[call-arg]
        )


def test_run_configuration_rejects_secret_and_machine_local_fields() -> None:
    raw = _configuration().model_dump()
    raw["environment"] = {"TOKEN": "not persisted"}

    with pytest.raises(ValidationError, match="environment"):
        RunConfiguration.model_validate(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_rounds", 0),
        ("max_retries_per_round", 0),
        ("judge_every", 0),
        ("official_eval_every", 0),
        ("cli_timeout", 0),
        ("max_retries_per_round", "3"),
    ],
)
def test_run_configuration_strictly_validates_positive_counts(
    field: str,
    value: object,
) -> None:
    raw = _configuration().model_dump()
    raw[field] = value

    with pytest.raises(ValidationError, match=field):
        RunConfiguration.model_validate(raw)


@pytest.mark.parametrize("field", ["inner_loop", "interface", "max_retries_per_round"])
def test_run_configuration_requires_core_agent_loop_behavior(field: str) -> None:
    raw = _configuration().model_dump()
    del raw[field]

    with pytest.raises(ValidationError, match=field):
        RunConfiguration.model_validate(raw)


def test_run_configuration_allows_optional_behavior_overrides_to_be_absent() -> None:
    raw = _configuration().model_dump()
    for field in (
        "model",
        "cli_provider",
        "cli_timeout",
        "profiler",
        "modality",
        "default_reasoning_effort",
        "outer_model",
        "outer_reasoning_effort",
        "inner_model",
        "inner_reasoning_effort",
    ):
        raw[field] = None

    configuration = RunConfiguration.model_validate(raw)

    assert configuration.cli_timeout is None
    assert configuration.modality is None
    assert configuration.outer_model is None
    assert configuration.inner_reasoning_effort is None


def test_run_configuration_is_frozen() -> None:
    configuration = _configuration()

    with pytest.raises(ValidationError, match="frozen"):
        configuration.inner_loop = "single-agent"


def test_create_project_writes_portable_committed_manifest(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "queue.rs").write_text("pub struct Queue;\n", encoding="utf-8")
    store = ProjectStore(tmp_path)

    manifest = store.create_project("Queue SPSC", now=NOW)

    assert store.load_project() == manifest
    assert store.metadata_gitignore_path.read_text(encoding="utf-8") == "/local/\n"
    raw = json.loads(store.project_manifest_path.read_text(encoding="utf-8"))
    assert raw == {
        "created_at": "2026-08-11T12:34:56Z",
        "initial_input_fingerprint": manifest.initial_input_fingerprint,
        "project_id": manifest.project_id,
        "schema_version": PROJECT_SCHEMA_VERSION,
    }
    serialized = store.project_manifest_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert "provider" not in serialized


def test_create_project_is_idempotent_after_source_changes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = store.load_project()
    (tmp_path / "src.py").write_text("changed = True\n", encoding="utf-8")

    assert store.create_project("A different display name", now=NOW + timedelta(days=1)) == original
    assert store.metadata_gitignore_path.read_text(encoding="utf-8") == "/local/\n"


def test_create_project_preserves_existing_metadata_ignore_rules(tmp_path: Path) -> None:
    metadata_dir = tmp_path / ".vs"
    metadata_dir.mkdir()
    (metadata_dir / ".gitignore").write_text("custom.tmp\n", encoding="utf-8")
    store = ProjectStore(tmp_path)

    store.create_project("queue", now=NOW)
    store.create_project("queue", now=NOW)

    assert store.metadata_gitignore_path.read_text(encoding="utf-8") == ("custom.tmp\n/local/\n")


def test_create_project_rejects_symlinked_metadata_root_before_writing(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "OBJECTIVE.md").write_text("Make it fast.\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    store = ProjectStore(project)
    store.metadata_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectStateError, match=r"metadata root must not be a symlink.*\.vs"):
        store.create_project("Queue SPSC", now=NOW)

    assert list(outside.iterdir()) == []


def test_create_run_rejects_symlinked_local_root_before_writing(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = _store(project)
    manifest = store.new_run_manifest(
        "Queue SPSC",
        branch="vibesys/queue",
        vibesys_version="0.2.0",
        configuration=_configuration(),
        trusted_input_baseline="a" * 40,
        now=NOW,
        unique=UNIQUE,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    store.local_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectStateError, match="local metadata root must not be a symlink"):
        store.create_run(manifest)

    assert not (store.metadata_dir / "runs").exists()
    assert list(outside.iterdir()) == []


def test_input_fingerprint_tracks_portable_files_only(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "queue.rs"
    source.write_text("one", encoding="utf-8")
    store = ProjectStore(tmp_path)
    initial = store.input_fingerprint()

    excluded_files = [
        tmp_path / ".env",
        tmp_path / ".env.local",
        tmp_path / "agent.toml",
        tmp_path / ".git" / "HEAD",
        tmp_path / ".vs" / "project.json",
        tmp_path / ".pytest_cache" / "state",
        tmp_path / "__pycache__" / "module.pyc",
    ]
    for path in excluded_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret-or-cache", encoding="utf-8")

    assert store.input_fingerprint() == initial
    source.write_text("two", encoding="utf-8")
    assert store.input_fingerprint() != initial


def test_run_manifest_and_local_state_use_separate_trees(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _run(store)

    assert store.load_run(manifest.run_id) == manifest
    assert store.run_manifest_path(manifest.run_id) == (
        tmp_path / ".vs" / "runs" / manifest.run_id / "run.json"
    )
    assert store.logs_dir(manifest.run_id) == (
        tmp_path / ".vs" / "local" / "runs" / manifest.run_id / "logs"
    )
    assert store.active_state_path(manifest.run_id) == (
        tmp_path / ".vs" / "local" / "runs" / manifest.run_id / "active.json"
    )
    assert store.logs_dir(manifest.run_id).is_dir()
    committed = store.run_manifest_path(manifest.run_id).read_text(encoding="utf-8")
    assert str(tmp_path) not in committed
    assert "token" not in committed


def test_run_manifest_persists_complete_agent_loop_behavior(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _run(store)

    raw = json.loads(store.run_manifest_path(manifest.run_id).read_text(encoding="utf-8"))

    assert raw["trusted_input_baseline"] == "a" * 40
    assert raw["configuration"] == {
        "agent_backend": "cli",
        "cli_provider": "codex",
        "cli_timeout": 1800,
        "compute_backend": "cpu",
        "default_reasoning_effort": "high",
        "inner_loop": "multi-agent",
        "inner_model": "gpt-5.6-luna",
        "inner_reasoning_effort": "medium",
        "interface": "inprocess",
        "judge_every": 3,
        "max_retries_per_round": 3,
        "max_rounds": 10,
        "memory_layout": "files",
        "modality": "text_generation",
        "model": "gpt-5",
        "official_eval_every": 3,
        "operator_constraints": ["Do not change the ABI"],
        "outer_loop": "agent",
        "outer_model": "gpt-5.6-sol",
        "outer_reasoning_effort": "xhigh",
        "profiler": "linux-cpu",
    }


@pytest.mark.parametrize("object_id", ["a" * 40, "b" * 64])
def test_run_manifest_accepts_git_sha1_and_sha256_object_ids(
    tmp_path: Path,
    object_id: str,
) -> None:
    store = _store(tmp_path)

    manifest = store.new_run_manifest(
        "queue",
        branch="vibesys/queue",
        vibesys_version="0.2.0",
        configuration=_configuration(),
        trusted_input_baseline=object_id,
        now=NOW,
        unique=UNIQUE,
    )

    assert manifest.trusted_input_baseline == object_id


@pytest.mark.parametrize("object_id", ["a" * 39, "A" * 40, "b" * 63, "../baseline"])
def test_run_manifest_rejects_invalid_git_object_ids(
    tmp_path: Path,
    object_id: str,
) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValidationError, match="trusted_input_baseline"):
        store.new_run_manifest(
            "queue",
            branch="vibesys/queue",
            vibesys_version="0.2.0",
            configuration=_configuration(),
            trusted_input_baseline=object_id,
            now=NOW,
            unique=UNIQUE,
        )


def test_update_run_configuration_preserves_manifest_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _run(store)
    updated_configuration = manifest.configuration.model_copy(update={"max_rounds": 20})

    path = store.update_run_configuration(manifest.run_id, updated_configuration)
    updated = store.load_run(manifest.run_id)

    assert path == store.run_manifest_path(manifest.run_id)
    assert updated.configuration == updated_configuration
    assert updated.model_copy(update={"configuration": manifest.configuration}) == manifest


def test_new_run_manifest_accepts_a_preallocated_safe_run_id(tmp_path: Path) -> None:
    store = _store(tmp_path)

    manifest = store.new_run_manifest(
        "queue",
        branch="vibesys/preallocated-run",
        vibesys_version="0.2.0",
        configuration=_configuration(),
        trusted_input_baseline="b" * 40,
        run_id="preallocated-run",
        now=NOW,
    )

    assert manifest.run_id == "preallocated-run"


def test_new_run_manifest_rejects_an_unsafe_preallocated_run_id(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ProjectStateError, match="Invalid VibeSys run ID"):
        store.new_run_manifest(
            "queue",
            branch="vibesys/queue",
            vibesys_version="0.2.0",
            configuration=_configuration(),
            trusted_input_baseline="b" * 40,
            run_id="../escape",
            now=NOW,
        )


def test_create_run_rejects_a_manifest_for_another_project(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = store.new_run_manifest(
        "queue",
        branch="vibesys/queue",
        vibesys_version="0.2.0",
        configuration=_configuration(),
        trusted_input_baseline="c" * 40,
        now=NOW,
        unique=UNIQUE,
    ).model_copy(update={"project_id": "another-project"})

    with pytest.raises(ProjectStateError, match="belongs to project"):
        store.create_run(manifest)


def test_current_and_latest_run_resolution(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _run(store, minute=1)
    second = _run(store, minute=2)

    assert store.list_runs() == [first, second]
    assert store.latest_run() == second
    assert store.resolve_run() == second
    store.set_current_run(first.run_id)
    assert store.current_run_id() == first.run_id
    assert store.resolve_run() == first
    assert store.resolve_run(second.run_id) == second
    store.set_current_run(None)
    assert store.current_run_id() is None
    assert store.resolve_run() == second


def test_resolve_run_without_runs_is_actionable(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ProjectStateError, match=r"No VibeSys runs.*\.vs"):
        store.resolve_run()


@pytest.mark.parametrize("run_id", ["../escape", "/absolute", "Uppercase", "", "a/b"])
def test_run_id_validation_prevents_path_escape(tmp_path: Path, run_id: str) -> None:
    store = _store(tmp_path)

    with pytest.raises(ProjectStateError, match="Invalid VibeSys run ID"):
        store.run_manifest_path(run_id)


def test_containment_rejects_symlinked_run_directory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    runs_dir = tmp_path / ".vs" / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "escaped").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectStateError, match="escapes"):
        store.run_manifest_path("escaped")


def test_round_record_serializer_is_public_and_round_trips() -> None:
    record = RoundRecord(
        round_number=7,
        commit="a" * 40,
        perf_metric=123.0,
        perf_unit="ops/s",
        passed=True,
        official_evaluation=True,
    )

    payload = serialize_round_record(record)

    assert payload["round"] == 7
    assert "round_number" not in payload
    assert parse_round_record(payload) == record


def test_project_state_serializes_validated_canonical_round_bytes() -> None:
    record = RoundRecord(
        round_number=7,
        commit="a" * 40,
        perf_metric=123.0,
        perf_unit="ops/s",
        passed=True,
        official_evaluation=True,
    )

    contents = serialize_round(record)

    assert contents.endswith(b"\n")
    assert json.loads(contents) == serialize_round_record(record)
    assert contents == serialize_round(record)


def test_project_state_serializer_rejects_non_portable_round_without_writing() -> None:
    record = RoundRecord(
        round_number=1,
        commit="a" * 40,
        perf_metric=123.0,
        perf_unit="ops/s",
        passed=True,
        evaluation_artifact="/host/result.json",
    )

    with pytest.raises(ProjectStateError, match="portable project-relative path"):
        serialize_round(record)


def test_completed_rounds_use_one_file_per_round_and_are_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    second = RoundRecord(2, "b" * 40, 20.0, "ops/s", True)  # noqa: FBT003
    first = RoundRecord(1, "a" * 40, 10.0, "ops/s", True)  # noqa: FBT003

    first_path = store.save_round(run.run_id, first)
    second_path = store.save_round(run.run_id, second)
    assert store.save_round(run.run_id, first) == first_path

    assert first_path.name == "0001.json"
    assert second_path.name == "0002.json"
    assert store.load_rounds(run.run_id) == [first, second]
    assert json.loads(first_path.read_text(encoding="utf-8"))["round"] == 1
    assert first_path.read_bytes() == serialize_round(first)


def test_completed_rounds_must_be_saved_in_append_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    first = RoundRecord(1, "a" * 40, 10.0, "ops/s", True)  # noqa: FBT003
    second = RoundRecord(2, "b" * 40, 20.0, "ops/s", True)  # noqa: FBT003
    third = RoundRecord(3, "c" * 40, 30.0, "ops/s", True)  # noqa: FBT003

    with pytest.raises(ProjectStateError, match="expected round 1, got 2"):
        store.save_round(run.run_id, second)

    store.save_round(run.run_id, first)
    with pytest.raises(ProjectStateError, match="expected round 2, got 3"):
        store.save_round(run.run_id, third)


def test_loaded_rounds_must_form_contiguous_sequence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    directory = store.rounds_dir(run.run_id)
    directory.mkdir()
    for round_number in (1, 3):
        record = RoundRecord(
            round_number,
            str(round_number) * 40,
            float(round_number),
            "ops/s",
            True,  # noqa: FBT003
        )
        (directory / f"{round_number:04d}.json").write_text(
            json.dumps(serialize_round_record(record)),
            encoding="utf-8",
        )

    with pytest.raises(ProjectStateError, match="expected round 2, found 3"):
        store.load_rounds(run.run_id)


def test_completed_round_cannot_be_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    original = RoundRecord(1, "a" * 40, 10.0, "ops/s", True)  # noqa: FBT003
    conflicting = RoundRecord(1, "b" * 40, 11.0, "ops/s", True)  # noqa: FBT003
    store.save_round(run.run_id, original)

    with pytest.raises(ProjectStateError, match="already exists with different data"):
        store.save_round(run.run_id, conflicting)


@pytest.mark.parametrize(
    "artifact",
    ["/absolute/result.json", "../result.json", "results\\host.json", ""],
)
def test_completed_round_rejects_machine_local_artifact_paths(
    tmp_path: Path, artifact: str
) -> None:
    store = _store(tmp_path)
    run = _run(store)
    record = RoundRecord(
        1,
        "a" * 40,
        10.0,
        "ops/s",
        True,  # noqa: FBT003
        evaluation_artifact=artifact,
    )

    with pytest.raises(ProjectStateError, match="portable project-relative path"):
        store.save_round(run.run_id, record)


def test_completed_round_rejects_non_finite_metrics(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    record = RoundRecord(1, "a" * 40, float("nan"), "ops/s", True)  # noqa: FBT003

    with pytest.raises(ProjectStateError, match="finite numbers"):
        store.save_round(run.run_id, record)


def test_loaded_round_rejects_machine_local_artifact_path(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    path = store.rounds_dir(run.run_id) / "0001.json"
    path.parent.mkdir()
    record = RoundRecord(
        1,
        "a" * 40,
        10.0,
        "ops/s",
        True,  # noqa: FBT003
        evaluation_artifact="/absolute/result.json",
    )
    path.write_text(json.dumps(serialize_round_record(record)), encoding="utf-8")

    with pytest.raises(
        ProjectStateError,
        match=r"0001\.json.*portable project-relative path",
    ):
        store.load_rounds(run.run_id)


def test_loaded_round_rejects_non_finite_metrics(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    path = store.rounds_dir(run.run_id) / "0001.json"
    path.parent.mkdir()
    record = RoundRecord(1, "a" * 40, float("nan"), "ops/s", True)  # noqa: FBT003
    path.write_text(json.dumps(serialize_round_record(record)), encoding="utf-8")

    with pytest.raises(ProjectStateError, match=r"0001\.json.*finite numbers"):
        store.load_rounds(run.run_id)


def test_corrupt_metadata_error_names_the_path(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    path = store.run_manifest_path(run.run_id)
    path.write_text('{"schema_version": 99}', encoding="utf-8")

    with pytest.raises(ProjectStateError, match=r"Invalid VibeSys metadata.*run\.json"):
        store.load_run(run.run_id)


def test_unknown_round_field_is_rejected_with_its_path(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    path = store.rounds_dir(run.run_id) / "0001.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "round": 1,
                "commit": None,
                "perf_metric": None,
                "perf_unit": None,
                "passed": False,
                "surprise": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProjectStateError, match=r"Invalid completed-round.*0001\.json"):
        store.load_rounds(run.run_id)
