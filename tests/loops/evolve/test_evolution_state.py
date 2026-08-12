"""Pure conversion tests for evolutionary population state."""

import pytest

from vibesys.loops.evolve.population import Individual, Population
from vibesys.loops.evolve.state import population_from_snapshot, population_snapshot


def test_population_snapshot_conversion_preserves_domain_values() -> None:
    population = Population(
        [
            Individual(
                id=1,
                generation=0,
                parent_id=None,
                commit="a" * 40,
                perf_metric=10.0,
                perf_unit="ops/s",
                metrics={"throughput": 10.0},
                passed=True,
            ),
            Individual(
                id=2,
                generation=1,
                parent_id=1,
                inspiration_ids=[],
                commit=None,
                passed=False,
                feedback="incorrect",
            ),
        ]
    )

    restored = population_from_snapshot(population_snapshot(population))

    assert restored.all == population.all


def test_population_snapshot_revalidates_mutated_domain_values() -> None:
    individual = Individual(
        id=1,
        generation=0,
        parent_id=None,
        commit="a" * 40,
        perf_metric=1.0,
        passed=True,
    )
    individual.perf_metric = float("nan")

    with pytest.raises(ValueError, match="finite"):
        population_snapshot(Population([individual]))
