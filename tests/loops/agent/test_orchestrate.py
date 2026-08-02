"""Tests for vibesys.loops.agent — orchestrator-driven build loop."""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vibesys.agents import AgentRunner
from vibesys.domains.base import DomainName
from vibesys.errors import ConfigurationError
from vibesys.loops.agent import issue_board
from vibesys.loops.agent.loop import (
    _ActiveHypothesis,
    _backfill_revert_commit,
    _invoke_read_only_role,
    _RoundRecord,
    _terminal_workspace_notice,
    run_agent_loop,
)
from vibesys.profilers import ProfilerKind, ProfilerPreflightResult
from vibesys.run import GitTracker
from vibesys.schemas import (
    HypothesisOutcome,
    ImplementerResponse,
    JudgeResponse,
    OrchestratorPlan,
    PreRoundDecision,
    ProfilerSummary,
    Verdict,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def test_legacy_active_hypothesis_backfills_framework_revert_commit():
    state = _ActiveHypothesis(
        plan=OrchestratorPlan(
            task="restore parent",
            pass_criteria="review",
            revert_to_round=28,
            reasoning="resume an older run",
        ),
        started_round=34,
        parent_round=28,
        revert_applied=True,
    )
    records = [
        _RoundRecord(
            round_number=28,
            commit="a" * 40,
            perf_metric=None,
            perf_unit=None,
            passed=False,
        )
    ]

    assert _backfill_revert_commit(state, records) is True
    assert state.revert_commit == "a" * 40
    assert _backfill_revert_commit(state, records) is False


@pytest.fixture()
def ref_file(tmp_path):
    """Create a reference *file* (not dir) + an OBJECTIVE.md sibling.

    Using a single file avoids the model-weight lookup that a reference
    directory triggers, which keeps these tests independent of HF cache
    state.
    """
    model_dir = tmp_path / "input_model"
    model_dir.mkdir()
    ref = model_dir / "ref.py"
    ref.write_text("def predict(x): return x * 2\n")
    (model_dir / "OBJECTIVE.md").write_text("Maximize tok/s throughput.\n")
    (model_dir / "vibesys.input.toml").write_text(
        """
version = 1

[agent]
domain = "llm-serving"

[accuracy]
command = ["uv", "run", "python", "accuracy_checker/checker.py"]

[benchmark]
command = ["uv", "run", "python", "benchmark/benchmark.py"]
""".lstrip()
    )
    return str(ref)


def _make_orchestrate_runner(
    *,
    pre_decisions: list[PreRoundDecision] | None = None,
    plans: list[OrchestratorPlan] | None = None,
    implementer_outcomes: list[HypothesisOutcome] | None = None,
    judge_verdicts: list[str] | None = None,
    profiler_responses: list[ProfilerSummary] | None = None,
    implementer_perf_metrics: list[float | None] | None = None,
):
    """Build a MagicMock AgentRunner whose invoke() returns scripted responses.

    Arguments are consumed-in-order queues keyed by the agent kind / response
    class. Defaults: when the plan queue is exhausted, the harness returns a
    permissive no-op plan and lets the loop's ``max_rounds`` bound the test.
    Judge verdicts default to pass; the profiler is not called.
    """
    pre_q = list(pre_decisions or [])
    plan_q = list(plans or [])
    outcome_q = list(implementer_outcomes or [])
    judge_q = list(judge_verdicts or [])
    prof_q = list(profiler_responses or [])
    impl_perf_q = list(implementer_perf_metrics or [])
    counters = {"impl": 0, "judge": 0, "orch_pre": 0, "orch_plan": 0, "prof": 0}

    runner = MagicMock(spec=AgentRunner)
    runner.backend_name = "deepagents"

    def _invoke(*, kind, response_cls, fallback_factory, **kwargs):
        if kind == "orchestrator" and response_cls is PreRoundDecision:
            counters["orch_pre"] += 1
            if pre_q:
                return pre_q.pop(0)
            return PreRoundDecision(need_profile=False, profile_focus="", reasoning="default skip")
        if kind == "orchestrator" and response_cls is OrchestratorPlan:
            counters["orch_plan"] += 1
            if plan_q:
                return plan_q.pop(0)
            return OrchestratorPlan(
                task="noop (harness default)",
                pass_criteria="no criteria",
                reasoning="default noop plan — the loop's max_rounds bounds the test",
            )
        if kind == "implementer":
            counters["impl"] += 1
            outcome = outcome_q.pop(0) if outcome_q else HypothesisOutcome.NOMINATED
            perf_metric = impl_perf_q.pop(0) if impl_perf_q else None
            return ImplementerResponse(
                summary="Done.",
                expected_behavior="ok",
                hypothesis_outcome=outcome,
                evidence="targeted evidence",
                next_step="continue experiment" if outcome is HypothesisOutcome.CONTINUE else "",
                perf_metric=perf_metric,
                perf_unit="tok/s" if perf_metric is not None else None,
                metrics={"aggregate_throughput": perf_metric, "p99_latency_ms": 87.0}
                if perf_metric is not None
                else {},
                evaluation_artifact="benchmark/summary.json"
                if perf_metric is not None
                else None,
            )
        if kind == "judge":
            idx = counters["judge"]
            counters["judge"] += 1
            v = judge_q[idx] if idx < len(judge_q) else "pass"
            return JudgeResponse(
                analysis="ok",
                feedback="" if v == "pass" else "needs work",
                verdict=Verdict.PASS if v == "pass" else Verdict.FAIL,
            )
        if kind == "profiler":
            counters["prof"] += 1
            if prof_q:
                return prof_q.pop(0)
            return ProfilerSummary(
                analysis="ok",
                bottlenecks="none",
                suggestions="none",
            )
        raise AssertionError(f"unexpected kind: {kind}, response_cls={response_cls}")

    runner.invoke.side_effect = _invoke
    runner.counters = counters  # test introspection
    return runner


def _invoke_orchestrate(tmp_path, ref_file, runner, **kwargs):
    """Shared plumbing: patch context globals, run the loop, return result."""
    accuracy_gate_results = kwargs.pop("_accuracy_gate_results", None)
    defaults = dict(
        config={"model": {"name": "claude-sonnet-4-6"}},
        exp_name="test-orch",
        input_path=str(Path(ref_file).parent),
        accuracy_command="uv run python accuracy_checker/checker.py",
        benchmark_command="uv run python benchmark/benchmark.py",
        objective="Maximize tok/s throughput.",
        max_rounds=5,
        max_retries_per_round=2,
        domain=DomainName.LLM_SERVING,
    )
    defaults.update(kwargs)
    with (
        patch("vibesys.context.build_model", return_value="mock-model"),
        patch("vibesys.backends.cuda.LocalShellBackend"),
        patch("vibesys.context.build_agent_runner", return_value=runner),
        patch("vibesys.context.PROJECT_ROOT", tmp_path),
        patch(
            "vibesys.loops.agent.loop._run_framework_accuracy_gate",
            side_effect=accuracy_gate_results,
            return_value=None,
        ),
    ):
        return run_agent_loop(**defaults)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


def test_read_only_role_reverts_workspace_mutations_and_keeps_response():
    ctx = MagicMock()
    ctx.git.current_sha.return_value = "a" * 40
    ctx.git.pending_changes.side_effect = [["roadmap/index.md", "scratch.txt"], []]
    ctx.git.checkout_tree.return_value = True
    expected = OrchestratorPlan(
        task="next", pass_criteria="passes", reasoning="evidence supports next"
    )
    ctx.invoke.return_value = expected

    result = _invoke_read_only_role(
        ctx,
        role="orchestrator",
        checkpoint_label="round-2-plan-input",
        kind="orchestrator",
        system_prompt="plan",
        user_prompt="return JSON",
        response_cls=OrchestratorPlan,
        fallback_factory=lambda: expected,
    )

    assert result is expected
    ctx.snapshot_workspace.assert_called_once_with("round-2-plan-input")
    ctx.git.checkout_tree.assert_called_once_with("a" * 40, clean=True)
    ctx.lprint.assert_called_once()


def test_read_only_role_does_not_restore_clean_turn():
    ctx = MagicMock()
    ctx.git.current_sha.return_value = "b" * 40
    ctx.git.pending_changes.return_value = []
    expected = JudgeResponse(analysis="clean", feedback="", verdict=Verdict.PASS)
    ctx.invoke.return_value = expected

    result = _invoke_read_only_role(
        ctx,
        role="judge",
        checkpoint_label="round-2-judge-input",
        kind="judge",
        system_prompt="judge",
        user_prompt="return JSON",
        response_cls=JudgeResponse,
        fallback_factory=lambda: expected,
    )

    assert result is expected
    ctx.git.checkout_tree.assert_not_called()
    ctx.lprint.assert_not_called()


def test_read_only_role_preserves_allowed_roadmap_and_reverts_other_writes(tmp_path):
    experiment = tmp_path / "experiment"
    workspace = experiment / "workspace"
    roadmap = workspace / "roadmap" / "index.md"
    roadmap.parent.mkdir(parents=True)
    roadmap.write_text("initial roadmap\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=experiment, check=True)
    tracker = GitTracker(workspace, log=lambda _message: None)
    tracker.init(existing=False)

    expected = OrchestratorPlan(
        task="next", pass_criteria="passes", reasoning="evidence supports next"
    )

    def invoke(**_kwargs):
        roadmap.write_text("updated roadmap\n")
        (workspace / "main.py").write_text("unauthorized candidate edit\n")
        return expected

    logs: list[str] = []
    ctx = SimpleNamespace(
        workspace=workspace,
        git=tracker,
        invoke=invoke,
        snapshot_workspace=tracker.snapshot,
        lprint=logs.append,
    )

    result = _invoke_read_only_role(
        ctx,
        role="orchestrator",
        checkpoint_label="round-2-plan-input",
        allowed_workspace_paths=("roadmap/index.md",),
        kind="orchestrator",
        system_prompt="plan",
        user_prompt="return JSON",
        response_cls=OrchestratorPlan,
        fallback_factory=lambda: expected,
    )

    assert result is expected
    assert roadmap.read_text() == "updated roadmap\n"
    assert not (workspace / "main.py").exists()
    assert tracker.pending_changes() == ["roadmap/index.md"]
    assert any("main.py" in line for line in logs)


def test_pre_round_decision_accepts_booleans():
    d = PreRoundDecision(need_profile=True, profile_focus="decode kernels", reasoning="ok")
    assert d.need_profile is True
    assert d.profile_focus == "decode kernels"


def test_orchestrator_plan_revert_round_optional():
    p = OrchestratorPlan(
        task="redo",
        pass_criteria="passes tests",
        revert_to_round=3,
        reasoning="step back",
    )
    assert p.revert_to_round == 3


def test_terminal_workspace_notice_points_designer_to_hypothesis_parent():
    records = [
        _RoundRecord(28, "a" * 40, None, None, False),
        _RoundRecord(
            29,
            "b" * 40,
            None,
            None,
            False,
            reviewed=True,
            hypothesis_id="bad-scheduler",
            hypothesis_outcome="rejected",
        ),
        _RoundRecord(
            30,
            "c" * 40,
            None,
            None,
            False,
            reviewed=False,
            hypothesis_id="bad-scheduler",
            hypothesis_outcome="disproven",
            hypothesis_parent_round=28,
        ),
    ]

    notice = _terminal_workspace_notice(records)

    assert notice is not None
    assert "workspace edits are still present" in notice
    assert "recorded pre-hypothesis parent is round 28" in notice
    assert "revert_to_round=28" in notice


def test_terminal_workspace_notice_preserves_credible_continuation_checkpoint():
    records = [
        _RoundRecord(28, "a" * 40, None, None, False),
        _RoundRecord(
            34,
            "b" * 40,
            None,
            None,
            False,
            reviewed=False,
            hypothesis_id="host-autopsy",
            hypothesis_outcome="continue",
            hypothesis_parent_round=28,
        ),
        _RoundRecord(
            35,
            "c" * 40,
            None,
            None,
            False,
            reviewed=False,
            hypothesis_id="host-autopsy",
            hypothesis_outcome="continue",
            hypothesis_parent_round=28,
        ),
        _RoundRecord(
            36,
            "d" * 40,
            None,
            None,
            True,
            reviewed=True,
            hypothesis_id="host-autopsy",
            hypothesis_outcome="disproven",
            hypothesis_parent_round=28,
        ),
    ]

    notice = _terminal_workspace_notice(records)

    assert notice is not None
    assert "recorded pre-hypothesis parent is round 28" in notice
    assert "most recent earlier nonterminal checkpoint is round 35" in notice
    assert "preserve that checkpoint instead of discarding prior gains" in notice
    assert "An older implementation cannot be required to reproduce" in notice


def test_profiler_summary_perf_metric_optional():
    p = ProfilerSummary(analysis="a", bottlenecks="b", suggestions="s")
    assert p.perf_metric is None
    p2 = ProfilerSummary(
        analysis="a",
        bottlenecks="b",
        suggestions="s",
        perf_metric=12.5,
        perf_unit="tok/s",
    )
    assert p2.perf_metric == 12.5
    assert p2.perf_unit == "tok/s"


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


def test_progress_writes_orchestrator_plan(tmp_path):
    progress = tmp_path / "progress.md"
    plan = OrchestratorPlan(
        task="Build FastAPI server",
        pass_criteria="/health returns 200",
        reasoning="Round 1 cold start",
    )
    issue_board.append_orchestrator_plan(progress, 1, plan)
    text = progress.read_text()
    assert "Round 1 — Orchestrator (plan)" in text
    assert "Build FastAPI server" in text
    assert "/health returns 200" in text


def test_progress_writes_profiler_summary_with_perf(tmp_path):
    progress = tmp_path / "progress.md"
    summary = ProfilerSummary(
        analysis="launch-bound",
        bottlenecks="attention kernel 40%",
        suggestions="swap to flashinfer",
        perf_metric=8.2,
        perf_unit="req/s",
    )
    issue_board.append_profiler_summary(progress, 2, summary)
    text = progress.read_text()
    assert "Round 2 — Profiler" in text
    assert "perf_metric**: 8.2 req/s" in text
    assert "flashinfer" in text


def test_progress_append_implementer_and_judge(tmp_path):
    progress = tmp_path / "progress.md"
    issue_board.append_implementer(
        progress,
        3,
        1,
        ImplementerResponse(summary="added cuda graph", expected_behavior="replay works"),
    )
    issue_board.append_judge(
        progress,
        3,
        1,
        JudgeResponse(analysis="good", feedback="", verdict=Verdict.PASS),
    )
    text = progress.read_text()
    assert "Round 3 — Implementer (attempt 1)" in text
    assert "Round 3 — Judge (attempt 1)" in text
    assert "verdict**: pass" in text


def test_directory_memory_layout_splits_rounds_and_bounds_reads(tmp_path):
    roadmap, progress = issue_board.resolve_paths(tmp_path, "directories")
    issue_board.ensure_roadmap_file(roadmap)
    for round_number in range(1, 16):
        issue_board.append_pre_round_decision(
            progress,
            round_number,
            PreRoundDecision(
                need_profile=False,
                profile_focus="",
                reasoning=f"decision-{round_number}",
            ),
        )

    assert (roadmap / "index.md").exists()
    assert (progress / "round-0001.md").exists()
    assert (progress / "round-0015.md").exists()
    recent = issue_board.read_progress(progress)
    assert "## Round 11 —" not in recent
    assert "## Round 12 —" in recent
    assert "## Round 15 —" in recent


def test_framework_accuracy_gate_runs_manifest_command_and_records_pass(tmp_path):
    from vibesys.loops.agent.loop import _run_framework_accuracy_gate

    ctx = MagicMock()
    ctx.trusted_input_changes.return_value = []
    ctx.judge_accuracy_command = "trusted-check --profile hard"
    ctx.judge_backend.execute.return_value = SimpleNamespace(exit_code=0, output="PASS")
    progress = tmp_path / "progress.md"

    feedback = _run_framework_accuracy_gate(
        ctx,
        round_number=2,
        retry=1,
        progress_path=progress,
    )

    assert feedback is None
    ctx.judge_backend.execute.assert_called_once_with("trusted-check --profile hard")
    text = progress.read_text()
    assert "Framework accuracy gate" in text
    assert "verdict**: pass" in text
    assert "PASS" in text
    ctx.snapshot_workspace.assert_called_once_with("round-2-retry-1-framework-accuracy")


def test_framework_accuracy_gate_rejects_checker_failure(tmp_path):
    from vibesys.loops.agent.loop import _run_framework_accuracy_gate

    ctx = MagicMock()
    ctx.trusted_input_changes.return_value = []
    ctx.judge_accuracy_command = "trusted-check"
    ctx.judge_backend.execute.return_value = SimpleNamespace(exit_code=1, output="bad history")

    feedback = _run_framework_accuracy_gate(
        ctx,
        round_number=1,
        retry=2,
        progress_path=tmp_path / "progress.md",
    )

    assert "Framework accuracy gate failed" in feedback
    assert "bad history" in feedback


def test_framework_accuracy_gate_uses_manifest_timeout(tmp_path):
    from vibesys.loops.agent.loop import _run_framework_accuracy_gate

    ctx = MagicMock()
    ctx.trusted_input_changes.return_value = []
    ctx.judge_accuracy_command = "trusted-check"
    ctx.judge_backend.execute.return_value = SimpleNamespace(exit_code=0, output="PASS")

    feedback = _run_framework_accuracy_gate(
        ctx,
        round_number=1,
        retry=1,
        progress_path=tmp_path / "progress.md",
        timeout_seconds=300,
    )

    assert feedback is None
    ctx.judge_backend.execute.assert_called_once_with("trusted-check", timeout=300)


def test_framework_accuracy_gate_rejects_evaluator_changes_without_execution(tmp_path):
    from vibesys.loops.agent.loop import _run_framework_accuracy_gate

    ctx = MagicMock()
    ctx.trusted_input_changes.return_value = ["_input_libs/checker.go"]
    ctx.judge_accuracy_command = "trusted-check"

    feedback = _run_framework_accuracy_gate(
        ctx,
        round_number=1,
        retry=1,
        progress_path=tmp_path / "progress.md",
    )

    assert "Evaluator-owned files were modified" in feedback
    ctx.judge_backend.execute.assert_not_called()


def test_framework_accuracy_gate_rejects_changes_during_execution(tmp_path):
    from vibesys.loops.agent.loop import _run_framework_accuracy_gate

    ctx = MagicMock()
    ctx.trusted_input_changes.side_effect = [[], ["_input_libs/checker.go"]]
    ctx.judge_accuracy_command = "trusted-check"
    ctx.judge_backend.execute.return_value = SimpleNamespace(exit_code=0, output="PASS")

    feedback = _run_framework_accuracy_gate(
        ctx,
        round_number=1,
        retry=1,
        progress_path=tmp_path / "progress.md",
    )

    assert "changed during accuracy execution" in feedback


def test_framework_benchmark_extracts_declared_metric(tmp_path):
    from vibesys.input_manifest import BenchmarkResult
    from vibesys.loops.agent.loop import (
        _FRAMEWORK_BENCHMARK_END_MARKER,
        _FRAMEWORK_BENCHMARK_MARKER,
        _run_framework_benchmark,
    )

    ctx = MagicMock()
    ctx.judge_benchmark_command = "trusted-benchmark --repetitions 3"
    ctx.trusted_input_changes.side_effect = [[], []]
    ctx.judge_backend.execute.return_value = SimpleNamespace(
        exit_code=0,
        output=(
            f"benchmark diagnostics\n{_FRAMEWORK_BENCHMARK_MARKER}\n"
            f'[{{"total_ops_per_sec": 42.5}}]\n{_FRAMEWORK_BENCHMARK_END_MARKER}\n'
            "[stderr] benchmark diagnostics emitted after stdout"
        ),
    )

    feedback, metric = _run_framework_benchmark(
        ctx,
        result_spec=BenchmarkResult(
            json_argument="--output-json",
            metric="total_ops_per_sec",
        ),
        round_number=3,
        retry=1,
        progress_path=tmp_path / "progress.md",
        timeout_seconds=300,
    )

    assert feedback is None
    assert metric == 42.5
    executed = ctx.judge_backend.execute.call_args.args[0]
    assert ctx.judge_backend.execute.call_args.kwargs == {"timeout": 300}
    assert "trusted-benchmark --repetitions 3 --output-json" in executed
    assert "cat /tmp/vibesys-framework-benchmark-3-1.json" in executed
    assert _FRAMEWORK_BENCHMARK_END_MARKER in executed
    assert "total_ops_per_sec**: 42.5" in (tmp_path / "progress.md").read_text()


def test_framework_benchmark_prefers_top_level_metric_over_trial_diagnostics(tmp_path):
    from vibesys.input_manifest import BenchmarkResult
    from vibesys.loops.agent.loop import (
        _FRAMEWORK_BENCHMARK_END_MARKER,
        _FRAMEWORK_BENCHMARK_MARKER,
        _run_framework_benchmark,
    )

    ctx = MagicMock()
    ctx.judge_benchmark_command = "trusted-benchmark"
    ctx.trusted_input_changes.side_effect = [[], []]
    ctx.judge_backend.execute.return_value = SimpleNamespace(
        exit_code=0,
        output=(
            f"{_FRAMEWORK_BENCHMARK_MARKER}\n"
            '{"primary_value": 42.5, "trials": [{"primary_value": 41.0}, '
            '{"primary_value": 44.0}]}\n'
            f"{_FRAMEWORK_BENCHMARK_END_MARKER}"
        ),
    )

    feedback, metric = _run_framework_benchmark(
        ctx,
        result_spec=BenchmarkResult(json_argument="--json", metric="primary_value"),
        round_number=1,
        retry=1,
        progress_path=tmp_path / "progress.md",
    )

    assert feedback is None
    assert metric == 42.5


def test_framework_benchmark_rejects_ambiguous_metric(tmp_path):
    from vibesys.input_manifest import BenchmarkResult
    from vibesys.loops.agent.loop import (
        _FRAMEWORK_BENCHMARK_END_MARKER,
        _FRAMEWORK_BENCHMARK_MARKER,
        _run_framework_benchmark,
    )

    ctx = MagicMock()
    ctx.judge_benchmark_command = "trusted-benchmark"
    ctx.trusted_input_changes.side_effect = [[], []]
    ctx.judge_backend.execute.return_value = SimpleNamespace(
        exit_code=0,
        output=(
            f'{_FRAMEWORK_BENCHMARK_MARKER}\n[{{"ops": 1}}, {{"ops": 2}}]\n'
            f"{_FRAMEWORK_BENCHMARK_END_MARKER}"
        ),
    )

    feedback, metric = _run_framework_benchmark(
        ctx,
        result_spec=BenchmarkResult(json_argument="--json", metric="ops"),
        round_number=1,
        retry=1,
        progress_path=tmp_path / "progress.md",
    )

    assert metric is None
    assert "expected exactly one 'ops' field" in feedback


# ---------------------------------------------------------------------------
# Loop happy paths
# ---------------------------------------------------------------------------


def test_loop_round_one_no_profile_runs_one_round(tmp_path, ref_file):
    """Round 1 skips pre-round-decision (no existing code), proposes one task,
    implementer+judge both pass. With max_rounds=1 the loop stops there."""
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                task="Build FastAPI server",
                pass_criteria="/health returns 200",
                reasoning="cold start",
            ),
        ],
    )
    result = _invoke_orchestrate(tmp_path, ref_file, runner, max_rounds=1)
    assert result is True
    # No pre-round decision on round 1 (no existing code).
    assert runner.counters["orch_plan"] == 1
    assert runner.counters["impl"] == 1
    assert runner.counters["judge"] == 1
    assert runner.counters["prof"] == 0


def test_loop_judge_retry_then_pass(tmp_path, ref_file):
    """Judge fails once, implementer retries, judge passes. Loop bounded by max_rounds=1."""
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                task="Build server",
                pass_criteria="tests pass",
                reasoning="cold start",
            ),
        ],
        judge_verdicts=["fail", "pass"],
    )
    result = _invoke_orchestrate(tmp_path, ref_file, runner, max_rounds=1, max_retries_per_round=3)
    assert result is True
    assert runner.counters["impl"] == 2
    assert runner.counters["judge"] == 2


def test_loop_defers_judge_until_cadence_and_always_reviews_final_round(
    tmp_path, ref_file
):
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                hypothesis_id="graph-decode",
                hypothesis="graph replay removes launch overhead",
                task=f"continue graph work {round_number}",
                pass_criteria="activation evidence is real",
                reasoning="continue one causal experiment",
            )
            for round_number in range(1, 4)
        ],
        implementer_outcomes=[HypothesisOutcome.CONTINUE] * 3,
    )

    result = _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=3,
        judge_every=2,
    )

    assert result is True
    assert runner.counters["impl"] == 3
    assert runner.counters["judge"] == 2  # cadence round 2 + mandatory final round 3
    rounds_files = list((tmp_path / "exp_env").glob("*/logs/rounds.json"))
    rounds = __import__("json").loads(rounds_files[0].read_text())
    assert [round_data["reviewed"] for round_data in rounds] == [False, True, True]
    progress_files = list((tmp_path / "exp_env").glob("*/workspace/progress.md"))
    assert "Independent review deferred" in progress_files[0].read_text()


def test_nominated_candidate_gets_early_review(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        implementer_outcomes=[HypothesisOutcome.NOMINATED],
    )

    _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=2,
        judge_every=10,
    )

    assert runner.counters["judge"] >= 1


def test_supported_hypothesis_is_reviewed_without_global_gates_and_closes(
    tmp_path, ref_file
):
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                hypothesis_id="diagnostic-one",
                hypothesis="the diagnostic identifies the bottleneck",
                task="collect the scoped evidence",
                pass_criteria="retain the diagnostic artifact",
                reasoning="finish one bounded diagnostic",
            ),
            OrchestratorPlan(
                hypothesis_id="mechanism-two",
                hypothesis="a new mechanism can use that evidence",
                task="start the next experiment",
                pass_criteria="retain causal evidence",
                reasoning="the prior diagnostic is complete",
            ),
        ],
        implementer_outcomes=[
            HypothesisOutcome.SUPPORTED,
            HypothesisOutcome.SUPPORTED,
        ],
    )

    _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=2,
        judge_every=10,
        _accuracy_gate_results=[AssertionError("global gate should not run")],
    )

    assert runner.counters["orch_plan"] == 2
    assert runner.counters["impl"] == 2
    assert runner.counters["judge"] == 2
    rounds_file = next((tmp_path / "exp_env").glob("*/logs/rounds.json"))
    rounds = __import__("json").loads(rounds_file.read_text())
    assert [round_data["hypothesis_outcome"] for round_data in rounds] == [
        "proven",
        "proven",
    ]


def test_cadence_pass_keeps_a_continuing_hypothesis_active(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                hypothesis_id="multi-round-experiment",
                hypothesis="one causal claim needs multiple rounds",
                task="run the experiment",
                pass_criteria="retain auditable evidence",
                reasoning="start one bounded experiment",
            )
        ],
        implementer_outcomes=[
            HypothesisOutcome.CONTINUE,
            HypothesisOutcome.CONTINUE,
            HypothesisOutcome.NOMINATED,
        ],
    )

    _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=3,
        judge_every=2,
    )

    # Round 2's cadence review validates the provisional implementation but
    # must not hand design ownership back to the outer agent. The same inner
    # agent finishes the hypothesis and nominates it in round 3.
    assert runner.counters["orch_plan"] == 1
    assert runner.counters["impl"] == 3
    assert runner.counters["judge"] == 2
    rounds_file = next((tmp_path / "exp_env").glob("*/logs/rounds.json"))
    rounds = __import__("json").loads(rounds_file.read_text())
    assert [round_data["hypothesis_outcome"] for round_data in rounds] == [
        "continue",
        "continue",
        "proven",
    ]


def test_cadence_review_is_not_duplicated_for_provisional_retry(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                hypothesis_id="stable-hypothesis",
                hypothesis="same causal claim",
                task="continue the experiment",
                pass_criteria="retain causal evidence",
                reasoning="one hypothesis across rounds",
            ),
            OrchestratorPlan(
                hypothesis_id="replacement-hypothesis",
                hypothesis="next causal claim",
                task="finish the replacement experiment",
                pass_criteria="retain causal evidence",
                reasoning="the prior claim passed review",
            ),
        ],
        implementer_outcomes=[
            HypothesisOutcome.CONTINUE,
            HypothesisOutcome.CONTINUE,
            HypothesisOutcome.NOMINATED,
            HypothesisOutcome.CONTINUE,
            HypothesisOutcome.NOMINATED,
        ],
        judge_verdicts=["fail", "pass"],
    )

    _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=4,
        max_retries_per_round=2,
        judge_every=3,
    )

    # Round 3 receives one cadence review.  Its provisional retry carries the
    # feedback forward without paying for the same independent audit again.
    # The final-round nomination is still reviewed immediately.
    assert runner.counters["impl"] == 5
    assert runner.counters["judge"] == 2


def test_unreviewed_terminal_outcome_returns_control_to_designer(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                hypothesis_id="falsified-path",
                hypothesis="first claim",
                task="test first claim",
                pass_criteria="collect evidence",
                reasoning="first experiment",
            ),
            OrchestratorPlan(
                hypothesis_id="replacement-path",
                hypothesis="second claim",
                task="test second claim",
                pass_criteria="collect evidence",
                reasoning="replacement experiment",
            ),
        ],
        implementer_outcomes=[
            HypothesisOutcome.DISPROVEN,
            HypothesisOutcome.NOMINATED,
        ],
    )

    _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=2,
        judge_every=10,
    )

    assert runner.counters["orch_plan"] == 2
    assert runner.counters["judge"] == 1  # only the replacement, on the final round
    rounds_files = list((tmp_path / "exp_env").glob("*/logs/rounds.json"))
    rounds = __import__("json").loads(rounds_files[0].read_text())
    assert [round_data["hypothesis_id"] for round_data in rounds] == [
        "falsified-path",
        "replacement-path",
    ]
    assert [round_data["hypothesis_outcome"] for round_data in rounds] == [
        "disproven",
        "proven",
    ]
    plan_calls = [
        call
        for call in runner.invoke.call_args_list
        if call.kwargs.get("response_cls") is OrchestratorPlan
    ]
    assert "do not require another expensive benchmark" in plan_calls[1].kwargs[
        "system_prompt"
    ].replace("\n", " ")


def test_reviewed_disproof_skips_framework_gates(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        implementer_outcomes=[HypothesisOutcome.DISPROVEN],
        judge_verdicts=["pass"],
    )

    _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=1,
        judge_every=1,
        _accuracy_gate_results=[AssertionError("accuracy gate should not run")],
    )

    rounds_file = next((tmp_path / "exp_env").glob("*/logs/rounds.json"))
    rounds = __import__("json").loads(rounds_file.read_text())
    assert rounds[0]["passed"] is True
    assert rounds[0]["reviewed"] is True
    assert rounds[0]["hypothesis_outcome"] == "disproven"


def test_disproven_retry_after_failed_review_returns_control_to_designer(
    tmp_path, ref_file
):
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                hypothesis_id="falsified-after-review",
                hypothesis="first claim",
                task="test first claim",
                pass_criteria="collect evidence",
                reasoning="first experiment",
            ),
            OrchestratorPlan(
                hypothesis_id="replacement-after-review",
                hypothesis="second claim",
                task="test second claim",
                pass_criteria="collect evidence",
                reasoning="replacement experiment",
            ),
        ],
        implementer_outcomes=[
            HypothesisOutcome.NOMINATED,
            HypothesisOutcome.DISPROVEN,
            HypothesisOutcome.NOMINATED,
        ],
        judge_verdicts=["fail", "pass"],
    )

    _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=2,
        max_retries_per_round=2,
        judge_every=10,
    )

    assert runner.counters["orch_plan"] == 2
    assert runner.counters["impl"] == 3
    assert runner.counters["judge"] == 2
    rounds_file = next((tmp_path / "exp_env").glob("*/logs/rounds.json"))
    rounds = __import__("json").loads(rounds_file.read_text())
    assert [round_data["hypothesis_id"] for round_data in rounds] == [
        "falsified-after-review",
        "replacement-after-review",
    ]
    assert [round_data["reviewed"] for round_data in rounds] == [False, True]
    assert [round_data["hypothesis_outcome"] for round_data in rounds] == [
        "disproven",
        "proven",
    ]


def test_role_session_policy_is_explicit_and_hypothesis_scoped(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                hypothesis_id="stable-hypothesis",
                hypothesis="same claim",
                task="continue",
                pass_criteria="review",
                reasoning="same experiment",
            ),
            OrchestratorPlan(
                hypothesis_id="stable-hypothesis",
                hypothesis="same claim",
                task="finish",
                pass_criteria="review",
                reasoning="same experiment",
            ),
        ],
        implementer_outcomes=[
            HypothesisOutcome.CONTINUE,
            HypothesisOutcome.NOMINATED,
        ],
    )

    _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=2,
        judge_every=10,
    )

    calls = runner.invoke.call_args_list
    plan_calls = [
        call for call in calls if call.kwargs.get("response_cls") is OrchestratorPlan
    ]
    implementer_calls = [
        call for call in calls if call.kwargs.get("response_cls") is ImplementerResponse
    ]
    judge_calls = [call for call in calls if call.kwargs.get("response_cls") is JudgeResponse]
    # The outer designer hands off one causal claim and is not re-invoked
    # while the implementer reports that same hypothesis as continuing.
    assert len(plan_calls) == 1
    assert len(implementer_calls) == 2
    assert all(call.kwargs["reuse_session"] is False for call in plan_calls)
    assert all(call.kwargs["reuse_session"] is True for call in implementer_calls)
    assert {
        call.kwargs["session_key"] for call in implementer_calls
    } == {"hypothesis:stable-hypothesis"}
    assert "Required continuation from the previous round" not in implementer_calls[
        0
    ].kwargs["system_prompt"]
    assert "continue experiment" in implementer_calls[1].kwargs["system_prompt"]
    assert "do not merely restate prior work" in implementer_calls[1].kwargs["user_prompt"]
    assert all(call.kwargs["reuse_session"] is False for call in judge_calls)

    rounds_files = list((tmp_path / "exp_env").glob("*/logs/rounds.json"))
    rounds = __import__("json").loads(rounds_files[0].read_text())
    assert [round_data["hypothesis_id"] for round_data in rounds] == [
        "stable-hypothesis",
        "stable-hypothesis",
    ]
    assert [round_data["hypothesis_outcome"] for round_data in rounds] == [
        "continue",
        "proven",
    ]


def test_hypothesis_revert_is_applied_once_across_continuation_rounds(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                hypothesis_id="seed",
                task="establish parent",
                pass_criteria="review",
                reasoning="seed checkpoint",
            ),
            OrchestratorPlan(
                hypothesis_id="continued-repair",
                task="start from parent and continue",
                pass_criteria="review",
                revert_to_round=1,
                reasoning="discard a later branch once",
            ),
        ],
        implementer_outcomes=[
            HypothesisOutcome.NOMINATED,
            HypothesisOutcome.CONTINUE,
            HypothesisOutcome.NOMINATED,
        ],
    )

    with patch(
        "vibesys.run.git_tracker.GitTracker.checkout_tree", return_value=True
    ) as checkout_tree:
        _invoke_orchestrate(
            tmp_path,
            ref_file,
            runner,
            max_rounds=3,
            judge_every=10,
        )

    assert checkout_tree.call_count == 1
    repair_calls = [
        call
        for call in runner.invoke.call_args_list
        if call.kwargs.get("response_cls") is ImplementerResponse
        and "continued-repair" in call.kwargs["system_prompt"]
    ]
    assert len(repair_calls) == 2
    assert all(
        "already applied exactly once by the framework" in call.kwargs["system_prompt"]
        for call in repair_calls
    )
    assert all(
        "sandboxes may intentionally omit usable `.git` metadata"
        in call.kwargs["system_prompt"].replace("\n", " ")
        for call in repair_calls
    )
    assert all(
        "do not alter candidate artifacts or rerun benchmarks"
        in call.kwargs["system_prompt"].replace("\n", " ")
        for call in repair_calls
    )
    assert all(
        "reserve the next representative run for the post-change candidate"
        in call.kwargs["system_prompt"].replace("\n", " ")
        for call in repair_calls
    )
    judge_calls = [
        call
        for call in runner.invoke.call_args_list
        if call.kwargs.get("response_cls") is JudgeResponse
        and "continued-repair" in call.kwargs["system_prompt"]
    ]
    assert judge_calls
    assert all(
        "Framework-owned parent provenance" in call.kwargs["system_prompt"]
        for call in judge_calls
    )
    assert all(
        "do not fail solely because `git` cannot re-query it"
        in call.kwargs["system_prompt"]
        for call in judge_calls
    )
    assert all(
        "candidate-authored duplication is neither required nor stronger evidence"
        in call.kwargs["system_prompt"].replace("\n", " ")
        for call in judge_calls
    )
    assert all(
        "benchmarking the untouched parent again"
        in call.kwargs["system_prompt"].replace("\n", " ")
        for call in judge_calls
    )


def test_failed_hypothesis_revert_is_retried_and_not_claimed_as_applied(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                hypothesis_id="seed",
                task="establish parent",
                pass_criteria="review",
                reasoning="seed checkpoint",
            ),
            OrchestratorPlan(
                hypothesis_id="retry-rollback",
                task="start from parent",
                pass_criteria="review",
                revert_to_round=1,
                reasoning="restore parent",
            ),
        ],
        implementer_outcomes=[
            HypothesisOutcome.NOMINATED,
            HypothesisOutcome.CONTINUE,
            HypothesisOutcome.NOMINATED,
        ],
    )

    with patch(
        "vibesys.run.git_tracker.GitTracker.checkout_tree",
        side_effect=[False, True],
    ) as checkout_tree:
        _invoke_orchestrate(
            tmp_path,
            ref_file,
            runner,
            max_rounds=3,
            judge_every=10,
        )

    assert checkout_tree.call_count == 2
    retry_calls = [
        call
        for call in runner.invoke.call_args_list
        if call.kwargs.get("response_cls") is ImplementerResponse
        and "retry-rollback" in call.kwargs["system_prompt"]
    ]
    assert len(retry_calls) == 2
    assert "already applied exactly once" not in retry_calls[0].kwargs["system_prompt"]
    assert "already applied exactly once" in retry_calls[1].kwargs["system_prompt"]


def test_judge_audited_implementer_metrics_are_recorded(tmp_path, ref_file):
    runner = _make_orchestrate_runner(implementer_perf_metrics=[321.5])

    _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=1,
        judge_every=10,
    )

    rounds_files = list((tmp_path / "exp_env").glob("*/logs/rounds.json"))
    rounds = __import__("json").loads(rounds_files[0].read_text())
    assert rounds[0]["perf_metric"] == 321.5
    assert rounds[0]["perf_unit"] == "tok/s"
    assert rounds[0]["metrics"] == {
        "aggregate_throughput": 321.5,
        "p99_latency_ms": 87.0,
    }
    assert rounds[0]["evaluation_artifact"] == "benchmark/summary.json"
    assert rounds[0]["profile_skipped"] is False

    judge_call = next(
        call
        for call in runner.invoke.call_args_list
        if call.kwargs.get("response_cls") is JudgeResponse
    )
    assert "321.5 tok/s" in judge_call.kwargs["system_prompt"]
    assert "benchmark/summary.json" in judge_call.kwargs["system_prompt"]


def test_loop_retries_when_framework_accuracy_gate_fails(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        plans=[OrchestratorPlan(task="Build", pass_criteria="tests", reasoning="start")],
        judge_verdicts=["pass", "pass"],
    )

    result = _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=1,
        max_retries_per_round=2,
        _accuracy_gate_results=["checker rejected history", None],
    )

    assert result is True
    assert runner.counters["impl"] == 2
    assert runner.counters["judge"] == 2


def test_framework_gate_retry_preserves_judge_approved_metrics(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        plans=[OrchestratorPlan(task="Build", pass_criteria="tests", reasoning="start")],
        judge_verdicts=["pass", "pass"],
        implementer_perf_metrics=[321.5, None],
    )

    result = _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=1,
        max_retries_per_round=2,
        _accuracy_gate_results=["wrapper failed", None],
    )

    assert result is True
    rounds_files = list((tmp_path / "exp_env").glob("*/logs/rounds.json"))
    rounds = __import__("json").loads(rounds_files[0].read_text())
    assert rounds[0]["perf_metric"] == 321.5
    assert rounds[0]["perf_unit"] == "tok/s"
    assert rounds[0]["metrics"] == {
        "aggregate_throughput": 321.5,
        "p99_latency_ms": 87.0,
    }
    assert rounds[0]["evaluation_artifact"] == "benchmark/summary.json"
    assert rounds[0]["profile_skipped"] is False

    implementer_calls = [
        call
        for call in runner.invoke.call_args_list
        if call.kwargs.get("response_cls") is ImplementerResponse
    ]
    assert len(implementer_calls) == 2
    retry_prompt = implementer_calls[1].kwargs["system_prompt"]
    assert "Framework gate revalidation" in retry_prompt
    assert "321.5 tok/s" in retry_prompt
    assert "Do not modify candidate behavior or rerun" in retry_prompt


def test_loop_exhaustion_carries_to_next_round(tmp_path, ref_file):
    """Review exhaustion returns to the same implementer, not the designer."""
    seen_plan_prompts: list[str] = []
    seen_implementer_prompts: list[str] = []
    original_runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                task="Build the whole server with every optimization",
                pass_criteria="impossibly strict",
                reasoning="ambitious",
            ),
            OrchestratorPlan(
                task="Just get /health working",
                pass_criteria="/health returns 200",
                reasoning="backed off after exhaustion",
            ),
        ],
        judge_verdicts=["fail", "fail", "pass"],
    )

    # Wrap invoke so we can capture the orchestrator plan prompts.
    real_invoke = original_runner.invoke.side_effect

    def spy_invoke(*, kind, response_cls, **kwargs):
        if kind == "orchestrator" and response_cls is OrchestratorPlan:
            seen_plan_prompts.append(kwargs.get("system_prompt", ""))
        if kind == "implementer" and response_cls is ImplementerResponse:
            seen_implementer_prompts.append(kwargs.get("system_prompt", ""))
        return real_invoke(kind=kind, response_cls=response_cls, **kwargs)

    original_runner.invoke.side_effect = spy_invoke

    result = _invoke_orchestrate(
        tmp_path,
        ref_file,
        original_runner,
        max_rounds=2,
        max_retries_per_round=2,
    )
    assert result is True
    # 2 attempts on round 1 (both fail) + 1 attempt on round 2 (pass).
    assert original_runner.counters["impl"] == 3
    # The outer designer is hands-off. Round 2 reuses the active plan and the
    # persistent implementer receives the independent judge's last feedback.
    assert len(seen_plan_prompts) == 1
    assert "needs work" in seen_implementer_prompts[2]


def test_loop_orchestrator_requests_profile_before_plan(tmp_path, ref_file):
    """If PreRoundDecision.need_profile is True, profiler runs before the plan call."""
    call_order: list[str] = []
    profiler_prompts: list[str] = []
    runner = _make_orchestrate_runner(
        pre_decisions=[
            PreRoundDecision(need_profile=True, profile_focus="kernels", reasoning="need data"),
        ],
        plans=[
            # Round 1 cold-start plan (no pre-decision invoked on round 1).
            OrchestratorPlan(
                task="Build server",
                pass_criteria="ok",
                reasoning="start",
            ),
            # Round 2 plan — uses profiler summary.
            OrchestratorPlan(
                task="Optimize decode",
                pass_criteria="graph replay",
                reasoning="profile showed launch overhead",
            ),
        ],
        profiler_responses=[
            ProfilerSummary(
                analysis="launch-bound",
                bottlenecks="host-side sync",
                suggestions="cuda graph",
                perf_metric=5.0,
                perf_unit="req/s",
            ),
        ],
    )

    real_invoke = runner.invoke.side_effect

    def spy_invoke(*, kind, response_cls, **kwargs):
        if kind == "orchestrator" and response_cls is OrchestratorPlan:
            call_order.append("plan")
        elif kind == "profiler":
            call_order.append("profiler")
            profiler_prompts.append(kwargs.get("system_prompt", ""))
        elif kind == "orchestrator" and response_cls is PreRoundDecision:
            call_order.append("pre")
        return real_invoke(kind=kind, response_cls=response_cls, **kwargs)

    runner.invoke.side_effect = spy_invoke

    result = _invoke_orchestrate(tmp_path, ref_file, runner, max_rounds=2)
    assert result is True
    # Round 1 cold-start: no pre → just plan.
    # Round 2: pre → profiler → plan.
    assert call_order[:1] == ["plan"]
    assert "profiler" in call_order
    plan_idx = [i for i, c in enumerate(call_order) if c == "plan"]
    prof_idx = call_order.index("profiler")
    # Profiler must come BEFORE the round-2 plan call.
    assert prof_idx < plan_idx[1]
    assert "Recent campaign context" in profiler_prompts[0]
    assert "Round 1" in profiler_prompts[0]
    assert "Do not launch a duplicate expensive evaluation" in profiler_prompts[0]


def test_loop_skips_profiler_when_pre_round_decision_says_no(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        pre_decisions=[
            PreRoundDecision(need_profile=False, profile_focus="", reasoning="benchmark is enough"),
        ],
        plans=[
            OrchestratorPlan(task="Build server", pass_criteria="ok", reasoning="start"),
            OrchestratorPlan(task="Use benchmark evidence", pass_criteria="ok", reasoning="skip"),
        ],
    )

    result = _invoke_orchestrate(tmp_path, ref_file, runner, max_rounds=2)

    assert result is True
    assert runner.counters["orch_pre"] == 1
    assert runner.counters["prof"] == 0


def test_loop_skips_profiler_when_profiler_kind_is_none(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        pre_decisions=[
            PreRoundDecision(need_profile=True, profile_focus="kernels", reasoning="would help"),
        ],
        plans=[
            OrchestratorPlan(task="Build server", pass_criteria="ok", reasoning="start"),
            OrchestratorPlan(
                task="Use benchmark evidence", pass_criteria="ok", reasoning="disabled"
            ),
        ],
    )

    result = _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=2,
        profiler_kind=ProfilerKind.NONE,
    )

    assert result is True
    assert runner.counters["orch_pre"] == 1
    assert runner.counters["prof"] == 0


def test_loop_generic_auto_profiler_resolves_to_macos_cpu(tmp_path, ref_file):
    runner = _make_orchestrate_runner(
        pre_decisions=[
            PreRoundDecision(need_profile=True, profile_focus="kernels", reasoning="would help"),
        ],
        plans=[
            OrchestratorPlan(task="Build queue", pass_criteria="ok", reasoning="start"),
            OrchestratorPlan(
                task="Use benchmark evidence", pass_criteria="ok", reasoning="generic"
            ),
        ],
    )

    with (
        patch("vibesys.profilers.platform.system", return_value="Darwin"),
        patch(
            "vibesys.context.preflight_profiler_kind",
            lambda kind: ProfilerPreflightResult(kind, True),
        ),
    ):
        result = _invoke_orchestrate(
            tmp_path,
            ref_file,
            runner,
            max_rounds=2,
            domain=DomainName.GENERIC,
        )

    assert result is True
    assert runner.counters["orch_pre"] == 1
    assert runner.counters["prof"] == 1


def test_loop_runs_full_max_rounds_budget(tmp_path, ref_file):
    """With the ``done`` field removed, the loop always exhausts max_rounds.
    A single-round budget yields one implementer + judge call, no more."""
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                task="Build server",
                pass_criteria="ok",
                reasoning="round 1",
            )
        ],
    )
    result = _invoke_orchestrate(tmp_path, ref_file, runner, max_rounds=1)
    assert result is True
    assert runner.counters["impl"] == 1
    assert runner.counters["judge"] == 1


def test_loop_max_rounds_terminates(tmp_path, ref_file):
    """Loop exits after max_rounds and reports success (the loop always runs
    to budget; there is no early-stop signal)."""
    plans = [OrchestratorPlan(task=f"t{i}", pass_criteria="p", reasoning="r") for i in range(10)]
    runner = _make_orchestrate_runner(plans=plans)
    result = _invoke_orchestrate(tmp_path, ref_file, runner, max_rounds=3)
    assert result is True
    assert runner.counters["orch_plan"] == 3
    assert runner.counters["impl"] == 3


# ---------------------------------------------------------------------------
# CLI / OBJECTIVE.md discovery
# ---------------------------------------------------------------------------


def test_cli_loads_objective_md_from_ref_parent(tmp_path):
    from vibesys.input_manifest import load_input_bundle
    from vibesys.main import _load_objective

    bundle = tmp_path / "modelA"
    bundle.mkdir()
    (bundle / "OBJECTIVE.md").write_text("Maximize throughput (tok/s). Prefer CUDA graphs.\n")
    (bundle / "vibesys.input.toml").write_text(
        "version = 1\n\n"
        "[agent]\ndomain = 'llm-serving'\n\n"
        "[accuracy]\ncommand = ['uv', 'run', 'python', 'accuracy_checker/checker.py']\n\n"
        "[benchmark]\ncommand = ['uv', 'run', 'python', 'benchmark/benchmark.py']\n"
    )

    objective = _load_objective(load_input_bundle(bundle))
    assert "Maximize throughput" in objective


def test_cli_missing_objective_md_errors(tmp_path):
    from vibesys.input_manifest import load_input_bundle

    bundle = tmp_path / "modelB"
    bundle.mkdir()
    (bundle / "vibesys.input.toml").write_text(
        "version = 1\n\n"
        "[agent]\ndomain = 'llm-serving'\n\n"
        "[accuracy]\ncommand = ['uv', 'run', 'python', 'accuracy_checker/checker.py']\n\n"
        "[benchmark]\ncommand = ['uv', 'run', 'python', 'benchmark/benchmark.py']\n"
    )

    with pytest.raises(FileNotFoundError, match="OBJECTIVE.md"):
        load_input_bundle(bundle)


def test_cli_rejects_modal_with_nsys_profiler(tmp_path, ref_file):
    """--modal only supports torch profiler."""
    from vibesys.main import _build_agent_parser, _validate_agent

    parser = _build_agent_parser()
    validate_args = _validate_agent
    args = parser.parse_args(
        [
            "--input",
            str(Path(ref_file).parent),
            "--exp-name",
            "test",
            "--modal",
            "--profiler",
            "nsys",
        ]
    )
    with pytest.raises(ConfigurationError):
        validate_args(args)


# ---------------------------------------------------------------------------
# --resume semantics
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Roadmap + plateau detection
# ---------------------------------------------------------------------------


def test_ensure_roadmap_seeds_header_when_missing(tmp_path):
    from vibesys.loops.agent import issue_board

    p = tmp_path / "roadmap.md"
    assert not p.exists()
    issue_board.ensure_roadmap_file(p)
    assert p.exists()
    text = p.read_text()
    # The seed must scaffold the four sections so the orchestrator's first
    # round starts with a clear structure.
    assert "## Major" in text
    assert "## Minor" in text
    assert "## Done" in text
    assert "## Abandoned" in text


def test_ensure_roadmap_does_not_overwrite_existing(tmp_path):
    from vibesys.loops.agent import issue_board

    p = tmp_path / "roadmap.md"
    p.write_text("# my custom plan\n")
    issue_board.ensure_roadmap_file(p)
    assert p.read_text() == "# my custom plan\n"


def test_read_roadmap_returns_text(tmp_path):
    from vibesys.loops.agent import issue_board

    p = tmp_path / "roadmap.md"
    p.write_text("hello\n")
    assert issue_board.read_roadmap(p) == "hello\n"


def test_read_roadmap_missing_returns_empty(tmp_path):
    from vibesys.loops.agent import issue_board

    p = tmp_path / "nope.md"
    assert issue_board.read_roadmap(p) == ""


def _record(round_number: int, perf: float | None, unit: str = "tok/s"):
    """Build a _RoundRecord shorthand for plateau tests."""
    from vibesys.loops.agent.loop import _RoundRecord

    return _RoundRecord(
        round_number=round_number,
        commit=f"sha{round_number:03d}",
        perf_metric=perf,
        perf_unit=unit if perf is not None else None,
        passed=perf is not None,
    )


def test_detect_plateau_returns_none_when_too_few_rounds():
    from vibesys.loops.agent.loop import _detect_plateau

    # Two rounds is below the 3-round minimum streak.
    records = [_record(1, 40.0), _record(2, 41.0)]
    assert _detect_plateau(records) is None


def test_detect_plateau_fires_on_flat_perf_streak():
    from vibesys.loops.agent.loop import _detect_plateau

    # 41.0 vs 41.5 is ~1.2% spread — well under the 5% threshold.
    records = [_record(1, 41.0), _record(2, 41.5), _record(3, 41.2)]
    warning = _detect_plateau(records)
    assert warning is not None
    assert "rounds 1–3" in warning
    assert "tok/s" in warning


def test_detect_plateau_skips_when_perf_diverges():
    from vibesys.loops.agent.loop import _detect_plateau

    # 41.0 vs 116.0 is ~64% spread — clearly off-plateau.
    records = [_record(1, 41.0), _record(2, 116.0), _record(3, 114.5)]
    assert _detect_plateau(records) is None


def test_detect_plateau_ignores_rounds_without_perf():
    """Rounds where the profiler skipped or the round failed (perf=None) must
    not interrupt the streak — only valid measurements count."""
    from vibesys.loops.agent.loop import _detect_plateau

    records = [
        _record(1, 41.0),
        _record(2, None),  # profiler skipped or failed round
        _record(3, 41.3),
        _record(4, 41.1),
    ]
    warning = _detect_plateau(records)
    assert warning is not None
    assert "rounds 1–4" in warning


def test_detect_plateau_streak_must_be_recent():
    """A plateau early in the run that's followed by a clear win must NOT
    fire a warning on the next round — only the *last N* matter."""
    from vibesys.loops.agent.loop import _detect_plateau

    records = [
        _record(1, 41.0),  # plateau
        _record(2, 41.2),  # plateau
        _record(3, 41.1),  # plateau (would fire here)
        _record(4, 116.0),  # break
    ]
    # By round 4, the recent streak (rounds 2,3,4) spans 41.2-116.0 → no plateau.
    assert _detect_plateau(records) is None


def test_loop_creates_roadmap_md_in_workspace(tmp_path, ref_file):
    """The first round of a fresh run must seed roadmap.md in the workspace."""
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(
                task="Build server",
                pass_criteria="/health 200",
                reasoning="cold start",
            ),
        ],
    )
    result = _invoke_orchestrate(tmp_path, ref_file, runner, max_rounds=1)
    assert result is True
    # The workspace lives under exp_env/<run-dir>/workspace/.
    roadmap_files = list((tmp_path / "exp_env").glob("*/workspace/roadmap.md"))
    assert len(roadmap_files) == 1
    text = roadmap_files[0].read_text()
    assert "## Major" in text


def test_loop_can_create_scannable_directory_memory(tmp_path, ref_file):
    runner = _make_orchestrate_runner()

    result = _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        max_rounds=1,
        memory_layout="directories",
    )

    assert result is True
    workspaces = list((tmp_path / "exp_env").glob("*/workspace"))
    workspace = workspaces[0]
    assert (workspace / "roadmap" / "index.md").exists()
    round_log = workspace / "progress" / "round-0001.md"
    assert round_log.exists()
    assert "Framework" not in round_log.read_text()  # stub skips official commands


def test_loop_threads_roadmap_into_orchestrator_prompt(tmp_path, ref_file):
    """The orchestrator's plan prompt must include the current roadmap.md
    contents so the orchestrator can update them."""
    seen_prompts: list[str] = []
    runner = _make_orchestrate_runner(
        plans=[
            OrchestratorPlan(task="t", pass_criteria="p", reasoning="r"),
        ],
    )
    real = runner.invoke.side_effect

    def spy(*, kind, response_cls, **kwargs):
        if kind == "orchestrator" and response_cls is OrchestratorPlan:
            seen_prompts.append(kwargs.get("system_prompt", ""))
        return real(kind=kind, response_cls=response_cls, **kwargs)

    runner.invoke.side_effect = spy

    _invoke_orchestrate(tmp_path, ref_file, runner, max_rounds=1)
    assert len(seen_prompts) == 1
    prompt = seen_prompts[0]
    # Roadmap section header must be present, and so must the seed scaffold.
    assert "Roadmap" in prompt
    assert "Major" in prompt
    assert "roadmap.md" in prompt


def test_loop_threads_plateau_warning_into_prompt(tmp_path, ref_file):
    """When the prior rounds plateau on perf, the orchestrator's next prompt
    must include the plateau warning."""
    seen_prompts: list[str] = []
    # Five rounds: round 1 is cold-start (no profiler), rounds 2-4 produce
    # flat perf metrics, and round 5 is the round under test (its plan call
    # should see the plateau warning).
    plans = [
        OrchestratorPlan(task=f"r{i}", pass_criteria="p", reasoning=f"r{i}") for i in range(1, 6)
    ]
    runner = _make_orchestrate_runner(
        pre_decisions=[
            PreRoundDecision(need_profile=True, profile_focus="x", reasoning="ok"),
        ]
        * 4,  # rounds 2-5
        plans=plans,
        profiler_responses=[
            ProfilerSummary(
                analysis="a",
                bottlenecks="b",
                suggestions="s",
                perf_metric=42.0,
                perf_unit="tok/s",
            ),
            ProfilerSummary(
                analysis="a",
                bottlenecks="b",
                suggestions="s",
                perf_metric=42.1,
                perf_unit="tok/s",
            ),
            ProfilerSummary(
                analysis="a",
                bottlenecks="b",
                suggestions="s",
                perf_metric=41.9,
                perf_unit="tok/s",
            ),
            ProfilerSummary(
                analysis="a",
                bottlenecks="b",
                suggestions="s",
                perf_metric=42.05,
                perf_unit="tok/s",
            ),
        ],
    )
    real = runner.invoke.side_effect

    def spy(*, kind, response_cls, **kwargs):
        if kind == "orchestrator" and response_cls is OrchestratorPlan:
            seen_prompts.append(kwargs.get("system_prompt", ""))
        return real(kind=kind, response_cls=response_cls, **kwargs)

    runner.invoke.side_effect = spy

    _invoke_orchestrate(tmp_path, ref_file, runner, max_rounds=5)
    assert len(seen_prompts) == 5
    # Rounds 1-4 have <3 valid perf records before each plan call → no
    # warning yet (round 1: 0 perf; round 2: 0 perf; round 3: 1 perf; round 4: 2 perf).
    for i in range(4):
        assert "Plateau detected" not in seen_prompts[i], (
            f"round {i + 1} should not yet have plateau warning"
        )
    # Round 5 plan call sees rounds 1-4 in records (3 valid perf measurements
    # from rounds 2,3,4 — flat at 41.9-42.1) → warning fires.
    assert "Plateau detected" in seen_prompts[4]
    assert "refresh an analytical performance model" in seen_prompts[4]
    assert "unexplained residual" in seen_prompts[4]


def test_loop_resume_with_round_number_starts_there(tmp_path, ref_file):
    """--resume 4 starts the loop at round 4 (prior rounds were committed by previous run)."""
    # With start_round=4 and max_rounds=5 only rounds 4 and 5 execute.
    plans = [
        OrchestratorPlan(task="keep going", pass_criteria="tests pass", reasoning="round 4"),
        OrchestratorPlan(task="more work", pass_criteria="tests pass", reasoning="round 5"),
    ]
    runner = _make_orchestrate_runner(plans=plans)

    # Pre-seed an existing exp dir so the context init takes the `existing=True`
    # branch.
    exp_env = tmp_path / "exp_env"
    (exp_env / "20260422-000000-test-orch").mkdir(parents=True)
    # Minimal git setup so the context validation accepts the repo.
    import subprocess

    subprocess.run(
        ["git", "init"],
        cwd=exp_env / "20260422-000000-test-orch",
        capture_output=True,
        check=True,
    )
    ws = exp_env / "20260422-000000-test-orch" / "workspace"
    ws.mkdir()
    subprocess.run(["git", "init"], cwd=ws, capture_output=True, check=True)
    (ws / "dummy.txt").write_text("x")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "add", "-A"], cwd=ws, env={**env}, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"], cwd=ws, env={**env}, capture_output=True, check=True
    )

    result = _invoke_orchestrate(
        tmp_path,
        ref_file,
        runner,
        exp_name="20260422-000000-test-orch",
        existing=True,
        start_round=4,
        max_rounds=5,
    )
    assert result is True
    # Round 4 and 5 only: 2 plan calls (one task, one done).
    assert runner.counters["orch_plan"] == 2
