"""Filesystem contract tests for evolutionary population state."""

from unittest.mock import MagicMock

from vibesys.loops.evolve.population import Individual, Population
from vibesys.loops.evolve.state import EvolutionStateStore
from vibesys.run import RunState, RunStateNamespace
from vs_project import EvolveRunConfiguration, Project, RunEnvironmentRecord


def _store(tmp_path) -> EvolutionStateStore:  # noqa: ANN001
    project = Project.open(tmp_path)
    project.state.create_project("test")
    run = project.state.new_run_manifest(
        "test",
        run_id="run-1",
        branch="vibesys/run-1",
        vibesys_version="test",
        trusted_input_baseline="a" * 40,
        configuration=EvolveRunConfiguration(
            outer_loop="evolve",
            run_environment=RunEnvironmentRecord(name="local"),
            agent_backend="stub",
            compute_backend="cpu",
            max_generations=1,
            children_per_generation=1,
            k_top_inspirations=1,
            k_random_inspirations=1,
            selection_temperature=0.5,
            frontier_bias=0.7,
            bootstrap_max_attempts=1,
            keep_deployments=False,
            max_parallelism=1,
        ),
    )
    project.state.create_run(run)
    state = RunState(
        project,
        git=MagicMock(history_root=project.root, run_id=run.run_id),
        run_id=run.run_id,
    )
    return EvolutionStateStore(state.portable(RunStateNamespace.EVOLVE))


def test_evolution_state_store_distinguishes_empty_from_persisted(tmp_path) -> None:  # noqa: ANN001
    store = _store(tmp_path)
    assert store.load_population().all == []

    population = Population(
        [
            Individual(
                id=1,
                generation=0,
                parent_id=None,
                commit="a" * 40,
                perf_metric=10.0,
                perf_unit="ops/s",
                passed=True,
            )
        ]
    )
    store.save_population(population)

    assert store.load_population().all == population.all
