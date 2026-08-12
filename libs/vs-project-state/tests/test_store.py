from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from vs_loop_state import RoundRecord, parse_round_record, serialize_round_record
from vs_project_state import (
    PROJECT_SCHEMA_VERSION,
    AgentRunConfiguration,
    EvolveRunConfiguration,
    PlainRunConfiguration,
    ProjectManifest,
    ProjectStateError,
    ProjectStore,
    RunConfiguration,
    RunManifest,
    StateDocument,
    StateFile,
    StateModelNotFoundError,
    StateSnapshot,
    StateTransition,
    generate_run_id,
    serialize_round,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 8, 11, 12, 34, 56, tzinfo=UTC)
UNIQUE = UUID("12345678-1234-5678-1234-567812345678")
RUN_CONFIGURATION_ADAPTER = TypeAdapter(RunConfiguration)


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    round: int
    phase: str


def _configuration() -> AgentRunConfiguration:
    return AgentRunConfiguration(
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


def _plain_configuration() -> PlainRunConfiguration:
    return PlainRunConfiguration(
        model="gpt-5",
        outer_loop="plain",
        agent_backend="cli",
        cli_provider="codex",
        cli_timeout=1800,
        compute_backend="cpu",
        profiler="none",
        max_rounds=5,
        max_attempts_per_issue=3,
        max_issues_per_perf_eval=3,
    )


def _evolve_configuration() -> EvolveRunConfiguration:
    return EvolveRunConfiguration(
        model="gpt-5",
        outer_loop="evolve",
        agent_backend="cli",
        cli_provider="codex",
        cli_timeout=1800,
        compute_backend="cpu",
        profiler="none",
        modality="text_generation",
        max_generations=8,
        children_per_generation=2,
        k_top_inspirations=2,
        k_random_inspirations=2,
        selection_temperature=0.5,
        seed=17,
        search_policy="openevolve",
        openevolve_population_size=100,
        openevolve_archive_size=20,
        openevolve_num_islands=5,
        openevolve_migration_interval=50,
        openevolve_migration_rate=0.1,
        frontier_bias=0.7,
        bootstrap_max_attempts=5,
        keep_deployments=False,
        max_parallelism=1,
        objectives=("throughput:max", "memory:min"),
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
        RUN_CONFIGURATION_ADAPTER.validate_python(raw, strict=True)


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
        RUN_CONFIGURATION_ADAPTER.validate_python(raw, strict=True)


@pytest.mark.parametrize("field", ["inner_loop", "interface", "max_retries_per_round"])
def test_run_configuration_requires_core_agent_loop_behavior(field: str) -> None:
    raw = _configuration().model_dump()
    del raw[field]

    with pytest.raises(ValidationError, match=field):
        RUN_CONFIGURATION_ADAPTER.validate_python(raw, strict=True)


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

    configuration = RUN_CONFIGURATION_ADAPTER.validate_python(raw, strict=True)

    assert configuration.cli_timeout is None
    assert configuration.modality is None
    assert configuration.outer_model is None
    assert configuration.inner_reasoning_effort is None


def test_run_configuration_is_frozen() -> None:
    configuration = _configuration()

    with pytest.raises(ValidationError, match="frozen"):
        configuration.inner_loop = "single-agent"


@pytest.mark.parametrize(
    ("configuration", "expected_type"),
    [
        (_configuration(), AgentRunConfiguration),
        (_plain_configuration(), PlainRunConfiguration),
        (_evolve_configuration(), EvolveRunConfiguration),
    ],
)
def test_run_configuration_discriminates_outer_loop(
    configuration: RunConfiguration,
    expected_type: type[RunConfiguration],
) -> None:
    parsed = RUN_CONFIGURATION_ADAPTER.validate_python(
        configuration.model_dump(),
        strict=True,
    )

    assert type(parsed) is expected_type
    with pytest.raises(ValidationError, match="frozen"):
        parsed.agent_backend = "stub"


@pytest.mark.parametrize("outer_loop", [None, "unknown"])
def test_run_configuration_requires_known_outer_loop(outer_loop: str | None) -> None:
    raw = _configuration().model_dump()
    if outer_loop is None:
        del raw["outer_loop"]
    else:
        raw["outer_loop"] = outer_loop

    with pytest.raises(ValidationError, match="outer_loop"):
        RUN_CONFIGURATION_ADAPTER.validate_python(raw, strict=True)


def test_run_configuration_rejects_fields_from_another_loop() -> None:
    raw = _plain_configuration().model_dump()
    raw["inner_loop"] = "multi-agent"

    with pytest.raises(ValidationError, match="inner_loop"):
        RUN_CONFIGURATION_ADAPTER.validate_python(raw, strict=True)


@pytest.mark.parametrize(
    ("configuration", "field", "value"),
    [
        (_plain_configuration(), "max_attempts_per_issue", 0),
        (_plain_configuration(), "max_issues_per_perf_eval", "3"),
        (_evolve_configuration(), "max_generations", 0),
        (_evolve_configuration(), "k_top_inspirations", -1),
        (_evolve_configuration(), "selection_temperature", 0.0),
        (_evolve_configuration(), "selection_temperature", float("inf")),
        (_evolve_configuration(), "openevolve_migration_rate", 1.1),
        (_evolve_configuration(), "frontier_bias", -0.1),
        (_evolve_configuration(), "max_parallelism", 0),
    ],
)
def test_loop_specific_configuration_constraints(
    configuration: RunConfiguration,
    field: str,
    value: object,
) -> None:
    raw = configuration.model_dump()
    raw[field] = value

    with pytest.raises(ValidationError, match=field):
        RUN_CONFIGURATION_ADAPTER.validate_python(raw, strict=True)


def test_evolve_configuration_rejects_openevolve_settings_for_vibesys_policy() -> None:
    raw = _evolve_configuration().model_dump()
    raw["search_policy"] = "vibesys"

    with pytest.raises(ValidationError, match="OpenEvolve settings"):
        RUN_CONFIGURATION_ADAPTER.validate_python(raw, strict=True)


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
    assert store.rounds_dir(manifest.run_id) == (
        tmp_path / ".vs" / "runs" / manifest.run_id / "agent" / "rounds"
    )
    assert store.local_namespace(manifest.run_id, "agent").project_relative_path(
        "active.json"
    ) == PurePosixPath(f".vs/local/runs/{manifest.run_id}/agent/active.json")
    assert store.round_transaction_path(manifest.run_id) == (
        tmp_path / ".vs" / "local" / "runs" / manifest.run_id / "round-transaction.json"
    )
    assert store.worktrees_dir(manifest.run_id) == (
        tmp_path / ".vs" / "local" / "runs" / manifest.run_id / "worktrees"
    )
    assert store.logs_dir(manifest.run_id).is_dir()
    assert not (tmp_path / ".vs" / "runs" / manifest.run_id / "agent").exists()
    assert not (tmp_path / ".vs" / "local" / "runs" / manifest.run_id / "agent").exists()
    assert not store.worktrees_dir(manifest.run_id).exists()
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


@pytest.mark.parametrize(
    ("configuration", "expected_type"),
    [
        (_configuration(), AgentRunConfiguration),
        (_plain_configuration(), PlainRunConfiguration),
        (_evolve_configuration(), EvolveRunConfiguration),
    ],
)
def test_run_manifest_round_trips_each_outer_loop_configuration(
    tmp_path: Path,
    configuration: RunConfiguration,
    expected_type: type[RunConfiguration],
) -> None:
    store = _store(tmp_path)
    manifest = store.new_run_manifest(
        "queue",
        branch="vibesys/queue",
        vibesys_version="0.2.0",
        configuration=configuration,
        trusted_input_baseline="a" * 40,
        now=NOW,
        unique=UNIQUE,
    )

    store.create_run(manifest)
    loaded = store.load_run(manifest.run_id)

    assert type(loaded.configuration) is expected_type
    assert loaded.configuration == configuration


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


def test_update_run_configuration_rejects_outer_loop_change(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _run(store)

    with pytest.raises(ProjectStateError, match="uses outer loop 'agent', not 'plain'"):
        store.update_run_configuration(manifest.run_id, _plain_configuration())

    assert store.load_run(manifest.run_id) == manifest


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


def test_containment_rejects_in_tree_symlinked_run_directory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runs_dir = tmp_path / ".vs" / "runs"
    target = runs_dir / "target"
    target.mkdir(parents=True)
    (runs_dir / "alias").symlink_to(target, target_is_directory=True)

    with pytest.raises(ProjectStateError, match="must not be a symlink"):
        store.run_manifest_path("alias")


@pytest.mark.parametrize(
    "namespace",
    ["", ".", "..", "../agent", "/agent", "agent/state", "agent\\state", "Agent"],
)
def test_state_namespace_validation_prevents_path_escape(
    tmp_path: Path,
    namespace: str,
) -> None:
    store = _store(tmp_path)
    run = _run(store)

    with pytest.raises(ProjectStateError, match="Invalid VibeSys state namespace"):
        store.portable_namespace(run.run_id, namespace)
    with pytest.raises(ProjectStateError, match="Invalid VibeSys state namespace"):
        store.local_namespace(run.run_id, namespace)


@pytest.mark.parametrize("local", [False, True])
def test_state_namespace_rejects_symlink_aliases(tmp_path: Path, *, local: bool) -> None:
    store = _store(tmp_path)
    run = _run(store)
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = (
        tmp_path / ".vs" / "local" / "runs" / run.run_id
        if local
        else tmp_path / ".vs" / "runs" / run.run_id
    )
    parent.mkdir(parents=True, exist_ok=True)
    (parent / "unsafe").symlink_to(outside, target_is_directory=True)

    state_namespace = store.local_namespace if local else store.portable_namespace
    with pytest.raises(ProjectStateError, match=r"(?:escapes|must not be a symlink)"):
        state_namespace(run.run_id, "unsafe")


def test_state_namespace_rejects_in_tree_symlink_alias(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    parent = tmp_path / ".vs" / "runs" / run.run_id
    target = parent / "target"
    target.mkdir()
    (parent / "alias").symlink_to(target, target_is_directory=True)

    with pytest.raises(ProjectStateError, match="must not be a symlink"):
        store.portable_namespace(run.run_id, "alias")


def test_state_namespace_rejects_existing_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)

    with pytest.raises(ProjectStateError, match="is not a directory"):
        store.portable_namespace(run.run_id, "run.json")


def test_worktrees_directory_rejects_symlink_alias(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    outside = tmp_path / "outside"
    outside.mkdir()
    worktrees = tmp_path / ".vs" / "local" / "runs" / run.run_id / "worktrees"
    worktrees.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectStateError, match=r"(?:escapes|must not be a symlink)"):
        store.worktrees_dir(run.run_id)


def test_completed_round_directory_rejects_symlink_alias(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    outside = tmp_path / "outside"
    outside.mkdir()
    agent_dir = tmp_path / ".vs" / "runs" / run.run_id / "agent"
    agent_dir.mkdir()
    (agent_dir / "rounds").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectStateError, match=r"(?:escapes|must not be a symlink)"):
        store.save_round(
            run.run_id,
            RoundRecord(1, "a" * 40, 10.0, "ops/s", True),  # noqa: FBT003
        )

    assert list(outside.iterdir()) == []


def test_state_namespace_round_trips_strict_models_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    namespace = store.portable_namespace(run.run_id, "plain")
    cursor = _Cursor(round=3, phase="judge")

    namespace.save("cursor.json", cursor)

    assert namespace.load("cursor.json", _Cursor) == cursor
    assert namespace.load_optional("cursor.json", _Cursor) == cursor
    raw_path = namespace.external_directory() / "cursor.json"
    assert json.loads(raw_path.read_text(encoding="utf-8")) == {
        "phase": "judge",
        "round": 3,
    }
    assert not list(raw_path.parent.glob("*.tmp"))


def test_state_namespace_prepares_and_applies_exact_typed_transition(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    namespace = store.local_namespace(run.run_id, "agent")
    cursor = _Cursor(round=3, phase="judge")

    transition = namespace.transition("active.json", cursor)

    expected_path = PurePosixPath(f".vs/local/runs/{run.run_id}/agent/active.json")
    assert transition.project_relative_path == expected_path
    assert transition.next_document == StateDocument(
        project_relative_path=expected_path,
        contents=b'{\n  "phase": "judge",\n  "round": 3\n}\n',
    )
    assert namespace.load_optional("active.json", _Cursor) is None

    namespace.apply(transition)

    assert namespace.load("active.json", _Cursor) == cursor

    deletion = namespace.transition("active.json", None)
    assert deletion == StateTransition(
        project_relative_path=expected_path,
        next_document=None,
    )
    namespace.apply(deletion)
    assert namespace.load_optional("active.json", _Cursor) is None


def test_state_namespace_rejects_transition_for_another_namespace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    agent = store.local_namespace(run.run_id, "agent")
    plain = store.local_namespace(run.run_id, "plain")
    transition = plain.transition("cursor.json", _Cursor(round=1, phase="judge"))

    with pytest.raises(ProjectStateError, match="outside this namespace"):
        agent.apply(transition)

    assert plain.load_optional("cursor.json", _Cursor) is None


def test_typed_state_slot_rejects_schema_invalid_reconstructed_document(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    run = _run(store)
    slot = store.local_namespace(run.run_id, "plain").slot("cursor.json", _Cursor)
    path = slot.project_relative_path
    transition = StateTransition(
        project_relative_path=path,
        next_document=StateDocument(
            project_relative_path=path,
            contents=b'{"round":1,"unexpected":true}',
        ),
    )

    with pytest.raises(ProjectStateError, match="typed slot schema"):
        slot.apply(transition)

    assert slot.load_optional() is None


def test_state_document_and_transition_validate_external_construction() -> None:
    path = PurePosixPath(".vs/local/runs/run-1/agent/active.json")

    with pytest.raises(ValueError, match="JSON object"):
        StateDocument(project_relative_path=path, contents=b"not-json")
    with pytest.raises(TypeError, match="JSON object"):
        StateDocument(project_relative_path=path, contents=b"[]")
    with pytest.raises(ValueError, match="match its target"):
        StateTransition(
            project_relative_path=path,
            next_document=StateDocument(
                project_relative_path=PurePosixPath(".vs/local/runs/run-1/agent/other.json"),
                contents=b"{}",
            ),
        )


def test_state_namespace_distinguishes_missing_from_corrupt_optional_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    namespace = store.portable_namespace(run.run_id, "plain")

    assert namespace.load_optional("cursor.json", _Cursor) is None
    with pytest.raises(StateModelNotFoundError, match=r"state model does not exist.*cursor\.json"):
        namespace.load("cursor.json", _Cursor)

    path = namespace.external_directory() / "cursor.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ProjectStateError, match=r"Invalid VibeSys state model.*cursor\.json"):
        namespace.load_optional("cursor.json", _Cursor)


def test_state_namespace_rejects_unknown_model_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    namespace = store.portable_namespace(run.run_id, "plain")
    path = namespace.external_directory() / "cursor.json"
    path.write_text(
        json.dumps({"round": 1, "phase": "judge", "surprise": True}),
        encoding="utf-8",
    )

    with pytest.raises(
        ProjectStateError,
        match=r"Invalid VibeSys state model.*cursor\.json.*surprise",
    ):
        namespace.load("cursor.json", _Cursor)


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        ".",
        "../cursor.json",
        "/cursor.json",
        "nested/../cursor.json",
        "a//b.json",
        "a\\b.json",
    ],
)
def test_state_namespace_rejects_unsafe_relative_paths(
    tmp_path: Path,
    relative_path: str,
) -> None:
    store = _store(tmp_path)
    run = _run(store)
    namespace = store.portable_namespace(run.run_id, "plain")

    with pytest.raises(ProjectStateError, match=r"safe portable|non-empty portable"):
        namespace.save(relative_path, _Cursor(round=1, phase="judge"))


def test_state_namespace_rejects_symlinks_below_namespace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    namespace = store.portable_namespace(run.run_id, "plain")
    root = namespace.external_directory()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectStateError, match="must not contain symlinks"):
        namespace.snapshot()
    with pytest.raises(ProjectStateError, match=r"(?:escapes|must not be a symlink)"):
        namespace.save("nested/cursor.json", _Cursor(round=1, phase="judge"))

    assert list(outside.iterdir()) == []


def test_state_namespace_revalidates_its_root_before_every_operation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    namespace = store.portable_namespace(run.run_id, "plain")
    root = tmp_path / ".vs" / "runs" / run.run_id / "plain"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectStateError, match=r"(?:escapes|must not be a symlink)"):
        namespace.snapshot()


def test_state_namespace_delete_reports_presence_and_rejects_directories(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    namespace = store.local_namespace(run.run_id, "agent")
    namespace.save("active.json", _Cursor(round=1, phase="implementer"))

    assert namespace.delete("active.json") is True
    assert namespace.delete("active.json") is False
    namespace.external_directory("nested")
    with pytest.raises(ProjectStateError, match="not a file"):
        namespace.delete("nested")


def test_portable_state_snapshot_is_deterministic_and_namespace_relative(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    namespace = store.portable_namespace(run.run_id, "evolve")
    namespace.save("z.json", _Cursor(round=2, phase="profile"))
    namespace.save("nested/a.json", _Cursor(round=1, phase="judge"))

    root = namespace.external_directory()
    expected = StateSnapshot(
        namespace_root=PurePosixPath(f".vs/runs/{run.run_id}/evolve"),
        files=(
            StateFile(
                relative_path=PurePosixPath("nested/a.json"),
                contents=(root / "nested/a.json").read_bytes(),
            ),
            StateFile(
                relative_path=PurePosixPath("z.json"),
                contents=(root / "z.json").read_bytes(),
            ),
        ),
    )

    assert namespace.snapshot() == expected
    assert namespace.snapshot() == namespace.snapshot()


def test_empty_portable_namespace_has_an_empty_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)

    snapshot = store.portable_namespace(run.run_id, "runtime").snapshot()

    assert snapshot.namespace_root == PurePosixPath(f".vs/runs/{run.run_id}/runtime")
    assert snapshot.files == ()


def test_initialization_snapshot_contains_only_selected_run_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _run(store)
    second = _run(store, minute=1)

    snapshot = store.initialization_snapshot(first.run_id)

    assert snapshot.namespace_root == PurePosixPath(".vs")
    assert tuple(file.relative_path for file in snapshot.files) == (
        PurePosixPath(".gitignore"),
        PurePosixPath("project.json"),
        PurePosixPath(f"runs/{first.run_id}/run.json"),
    )
    assert PurePosixPath(f"runs/{second.run_id}/run.json") not in {
        file.relative_path for file in snapshot.files
    }
    assert snapshot.files[0].contents == store.metadata_gitignore_path.read_bytes()
    assert snapshot.files[1].contents == store.project_manifest_path.read_bytes()
    assert snapshot.files[2].contents == store.run_manifest_path(first.run_id).read_bytes()


def test_run_manifest_snapshot_is_rooted_at_the_selected_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)

    snapshot = store.run_manifest_snapshot(run.run_id)

    assert snapshot == StateSnapshot(
        namespace_root=PurePosixPath(f".vs/runs/{run.run_id}"),
        files=(
            StateFile(
                relative_path=PurePosixPath("run.json"),
                contents=store.run_manifest_path(run.run_id).read_bytes(),
            ),
        ),
    )


def test_completed_round_snapshot_contains_one_canonical_round(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    first = RoundRecord(1, "a" * 40, 10.0, "ops/s", True)  # noqa: FBT003
    second = RoundRecord(2, "b" * 40, 20.0, "ops/s", True)  # noqa: FBT003
    store.save_round(run.run_id, first)
    second_path = store.save_round(run.run_id, second)

    snapshot = store.completed_round_snapshot(run.run_id, 2)

    assert snapshot == StateSnapshot(
        namespace_root=PurePosixPath(f".vs/runs/{run.run_id}/agent"),
        files=(
            StateFile(
                relative_path=PurePosixPath("rounds/0002.json"),
                contents=second_path.read_bytes(),
            ),
        ),
    )
    assert snapshot.files[0].contents == serialize_round(second)


@pytest.mark.parametrize("round_number", [0, 1, 2])
def test_completed_round_snapshot_rejects_missing_or_invalid_round(
    tmp_path: Path,
    round_number: int,
) -> None:
    store = _store(tmp_path)
    run = _run(store)

    with pytest.raises(ProjectStateError, match=r"positive|does not exist"):
        store.completed_round_snapshot(run.run_id, round_number)


def test_metadata_snapshot_rejects_symlinked_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    outside = tmp_path / "outside"
    outside.write_text("local\n", encoding="utf-8")
    store.metadata_gitignore_path.unlink()
    store.metadata_gitignore_path.symlink_to(outside)

    with pytest.raises(ProjectStateError, match=r"(?:escapes|must not be a symlink)"):
        store.initialization_snapshot(run.run_id)


@pytest.mark.parametrize(
    "root",
    [
        PurePosixPath("elsewhere"),
        PurePosixPath(".vs/local"),
        PurePosixPath(".vs/runs"),
        PurePosixPath(".vs/runs/Uppercase"),
        PurePosixPath(".vs/runs/run-1/Uppercase"),
    ],
)
def test_state_snapshot_rejects_unsafe_or_local_roots(root: PurePosixPath) -> None:
    with pytest.raises(ValueError, match=r"portable state snapshot|invalid"):
        StateSnapshot(namespace_root=root, files=())


@pytest.mark.parametrize(
    "relative_path",
    [PurePosixPath("../secret"), PurePosixPath("/absolute"), PurePosixPath(".")],
)
def test_state_file_rejects_unsafe_relative_paths(relative_path: PurePosixPath) -> None:
    with pytest.raises(ValueError, match="portable relative path"):
        StateFile(relative_path=relative_path, contents=b"secret")


def test_state_snapshot_rejects_local_file_below_metadata_root() -> None:
    local_file = StateFile(
        relative_path=PurePosixPath("local/runs/run-1/state.json"),
        contents=b"{}",
    )

    with pytest.raises(ValueError, match=r"must not contain \.vs/local"):
        StateSnapshot(namespace_root=PurePosixPath(".vs"), files=(local_file,))


def test_machine_local_state_namespace_cannot_be_snapshotted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store)
    namespace = store.local_namespace(run.run_id, "agent")
    namespace.save("active.json", _Cursor(round=1, phase="implementer"))

    with pytest.raises(ProjectStateError, match=r"Machine-local.*cannot be snapshotted"):
        namespace.snapshot()


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
    directory.mkdir(parents=True)
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
    path.parent.mkdir(parents=True)
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
    path.parent.mkdir(parents=True)
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
    path.parent.mkdir(parents=True)
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
