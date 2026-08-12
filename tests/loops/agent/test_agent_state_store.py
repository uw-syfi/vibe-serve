"""Filesystem contract tests for the agent-loop state adapter."""

from vibesys.loops.agent.model import ActiveHypothesis
from vibesys.loops.agent.state import AgentStateStore
from vibesys.schemas import OrchestratorPlan
from vs_project_state import PlainRunConfiguration, ProjectStore, StateTransition


def test_agent_state_store_round_trips_and_clears_active_state(tmp_path) -> None:  # noqa: ANN001
    project = ProjectStore(tmp_path)
    project.create_project("test")
    run = project.new_run_manifest(
        "test",
        run_id="run-1",
        branch="vibesys/run-1",
        vibesys_version="test",
        trusted_input_baseline="a" * 40,
        configuration=PlainRunConfiguration(
            outer_loop="plain",
            agent_backend="stub",
            compute_backend="cpu",
            max_rounds=1,
            max_attempts_per_issue=1,
            max_issues_per_perf_eval=1,
        ),
    )
    project.create_run(run)
    namespace = project.local_namespace("run-1", "agent")
    store = AgentStateStore(namespace)
    state = ActiveHypothesis(
        plan=OrchestratorPlan(
            task="optimize the queue",
            pass_criteria="the checker passes",  # noqa: S106
            reasoning="reduce contention",
        ),
        started_round=1,
    )

    assert store.load_active() is None
    transition = store.prepare_active_transition(state)
    assert isinstance(transition, StateTransition)
    assert store.load_active() is None

    store.apply_active_transition(transition)
    assert store.load_active() == state

    deletion = store.prepare_active_transition(None)
    store.apply_active_transition(deletion)
    assert store.load_active() is None
