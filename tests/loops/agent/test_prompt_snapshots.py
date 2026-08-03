"""Snapshot tests for final rendered agent prompts.

These fixtures are intentionally plain text files. When prompt wording changes
on purpose, the fixture diff is the review artifact: it shows reviewers exactly
what an agent will see after all template includes and domain interpolation.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

import pytest

from vibesys.domains.base import DomainName
from vibesys.domains.registry import resolve_domain
from vibesys.domains.rendering import render_domain_section
from vibesys.profilers import ProfilerKind, profiler_definition
from vibesys.prompts import render_template

_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATE_DIR = _ROOT / "src" / "vibesys" / "loops" / "agent" / "templates"
_SNAPSHOT_DIR = Path(__file__).with_name("fixtures") / "prompt_snapshots"

_ROLES = ("implementer", "judge", "single_agent", "orchestrator")

_BASE_CONTEXT = {
    "modality": "text_generation",
    "reference_path": "/workspace/reference/main.py",
    "task": "TASK: add a streaming /v1/completions endpoint.",
    "pass_criteria": "PASS: pytest passes and /v1/completions streams valid SSE.",
    "objective": "OBJECTIVE: maximize median_tok_per_sec.",
    "roadmap_text": "- major-1: todo - establish the serving optimization floor.",
    "recent_progress_text": "# Round 7\n\nImplementer is still testing graph activation.",
    "progress_location": "progress/",
    "roadmap_location": "roadmap/",
    "hypothesis_id": "cuda-graph-decode",
    "hypothesis": "Removing decode launch overhead will improve median_tok_per_sec.",
    "activation_evidence": "cuda_graph_replays increases on steady requests.",
    "falsification_criteria": "Graphs replay but headline throughput does not improve.",
    "expected_effect": "Forecast 1.3x to 1.6x end-to-end throughput.",
    "minimum_acceptance_criteria": "Retain at >=1.15x throughput with no latency regression.",
    "invariants": "Accuracy and prompt-dependent generation remain unchanged.",
    "implementer_outcome": "nominated",
    "implementer_evidence": "Replay counter increased in a targeted probe.",
    "pareto_archive_summary": (
        "Configured axes: throughput:max, latency:min\n"
        "Trusted frontier parents:\n"
        "- round 6, commit abc123, reviewed provisional: "
        "throughput=120, latency=80"
    ),
    "env_kind": "local",
}

_CONTEXTS = {
    "full": _BASE_CONTEXT
    | {
        "benchmark_command": "uv run python benchmark/benchmark.py",
        "accuracy_command": "uv run python accuracy_checker/checker.py",
        "runtime_notes": "Runtime note: local Docker workspace with NVIDIA CUDA access.",
    },
    "minimal": _BASE_CONTEXT
    | {
        "benchmark_command": None,
        "accuracy_command": None,
        "runtime_notes": "",
    },
}


def _domain_context(context: dict[str, object]) -> dict[str, object]:
    return {
        "modality": context["modality"],
        "reference_path": context["reference_path"],
        "benchmark_command": context["benchmark_command"],
        "accuracy_command": context["accuracy_command"],
        "runtime_notes": context["runtime_notes"],
    }


def _domain_section(domain: DomainName, role: str, context: dict[str, object]) -> str:
    return render_domain_section(resolve_domain(domain), role, **_domain_context(context))


def _render_prompt(domain: DomainName, role: str, context: dict[str, object]) -> str:
    if role == "implementer":
        return render_template(
            "implementer_prompt.j2",
            template_dir=_TEMPLATE_DIR,
            modality=context["modality"],
            reference_path=context["reference_path"],
            runtime_notes=context["runtime_notes"],
            task=context["task"],
            pass_criteria=context["pass_criteria"],
            objective=context["objective"],
            feedback=None,
            hypothesis_id=context["hypothesis_id"],
            hypothesis=context["hypothesis"],
            activation_evidence=context["activation_evidence"],
            falsification_criteria=context["falsification_criteria"],
            expected_effect=context["expected_effect"],
            minimum_acceptance_criteria=context["minimum_acceptance_criteria"],
            invariants=context["invariants"],
            progress_location=context["progress_location"],
            domain_implementer=_domain_section(domain, "implementer", context),
            framework_benchmark_enabled=context.get("framework_benchmark_enabled", False),
            official_evaluation_due=context.get("official_evaluation_due", False),
            official_evaluation_reason=context.get("official_evaluation_reason"),
            pareto_archive_summary=context["pareto_archive_summary"],
        )
    if role == "judge":
        judge_domain_context = context | {
            "benchmark_command": None,
            "accuracy_command": None,
        }
        return render_template(
            "judge_prompt.j2",
            template_dir=_TEMPLATE_DIR,
            modality=context["modality"],
            objective=context["objective"],
            pass_criteria=context["pass_criteria"],
            runtime_notes=context["runtime_notes"],
            benchmark_command=context["benchmark_command"],
            accuracy_command=context["accuracy_command"],
            domain_judge=_domain_section(domain, "judge", judge_domain_context),
            hypothesis_id=context["hypothesis_id"],
            hypothesis=context["hypothesis"],
            activation_evidence=context["activation_evidence"],
            falsification_criteria=context["falsification_criteria"],
            expected_effect=context["expected_effect"],
            minimum_acceptance_criteria=context["minimum_acceptance_criteria"],
            invariants=context["invariants"],
            implementer_outcome=context["implementer_outcome"],
            implementer_evidence=context["implementer_evidence"],
            implementer_perf_metric=context.get("implementer_perf_metric"),
            gate_revalidation_pending=context.get("gate_revalidation_pending", False),
            progress_location=context["progress_location"],
            framework_benchmark_enabled=context.get("framework_benchmark_enabled", False),
            official_evaluation_due=context.get("official_evaluation_due", False),
            official_evaluation_reason=context.get("official_evaluation_reason"),
            pareto_archive_summary=context["pareto_archive_summary"],
        )
    if role == "single_agent":
        profiler = profiler_definition(ProfilerKind.NSYS)
        return render_template(
            "single_agent_round_prompt.j2",
            template_dir=_TEMPLATE_DIR,
            modality=context["modality"],
            env_kind=context["env_kind"],
            objective=context["objective"],
            runtime_notes=context["runtime_notes"],
            task=context["task"],
            pass_criteria=context["pass_criteria"],
            benchmark_command=context["benchmark_command"],
            accuracy_command=context["accuracy_command"],
            retry=1,
            feedback=None,
            reference_path=context["reference_path"],
            profiler_kind=ProfilerKind.NSYS,
            profiler_support_name=profiler.support_name,
            profiler_mcp_name=profiler.mcp_name,
            profile_focus="",
            domain_single_agent=_domain_section(domain, "single_agent", context),
            domain_profiler=_domain_section(domain, "profiler", context),
            hypothesis_id=context["hypothesis_id"],
            hypothesis=context["hypothesis"],
            activation_evidence=context["activation_evidence"],
            falsification_criteria=context["falsification_criteria"],
            expected_effect=context["expected_effect"],
            minimum_acceptance_criteria=context["minimum_acceptance_criteria"],
            invariants=context["invariants"],
            progress_location=context["progress_location"],
            framework_benchmark_enabled=context.get("framework_benchmark_enabled", False),
            official_evaluation_due=context.get("official_evaluation_due", False),
            official_evaluation_reason=context.get("official_evaluation_reason"),
            pareto_archive_summary=context["pareto_archive_summary"],
        )
    if role == "orchestrator":
        return render_template(
            "orchestrator_plan_prompt.j2",
            template_dir=_TEMPLATE_DIR,
            objective=context["objective"],
            profiler_summary=None,
            regression_info=None,
            exhaustion_info=None,
            roadmap_text=context["roadmap_text"],
            recent_progress_text=context["recent_progress_text"],
            progress_location=context["progress_location"],
            roadmap_location=context["roadmap_location"],
            plateau_warning=None,
            runtime_notes=context["runtime_notes"],
            env_kind=context["env_kind"],
            domain_orchestrator=_domain_section(domain, "orchestrator", context),
            official_eval_every=context.get("official_eval_every", 3),
            provisional_candidates=context.get("provisional_candidates", 0),
            official_eval_cadence_due=context.get("official_eval_cadence_due", False),
        )
    raise AssertionError(f"unknown prompt role: {role}")


def _snapshot_path(domain: str, case_name: str, role: str) -> Path:
    return _SNAPSHOT_DIR / domain / case_name / f"{role}.md"


def _assert_matches_snapshot(domain: str, case_name: str, role: str, rendered: str) -> None:
    snapshot = _snapshot_path(domain, case_name, role)
    if os.environ.get("UPDATE_PROMPT_SNAPSHOTS") == "1":
        snapshot.write_text(rendered)
        return
    expected = snapshot.read_text()
    if rendered == expected:
        return

    rendered_name = Path("rendered") / domain / case_name / f"{role}.md"
    diff = "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=str(snapshot),
            tofile=str(rendered_name),
        )
    )
    pytest.fail(f"Rendered prompt changed: {snapshot}\n{diff}")


@pytest.mark.parametrize("case_name,context", _CONTEXTS.items())
@pytest.mark.parametrize("role", _ROLES)
def test_llm_serving_prompt_snapshot(case_name: str, context: dict[str, object], role: str):
    rendered = _render_prompt(DomainName.LLM_SERVING, role, context)
    _assert_matches_snapshot(DomainName.LLM_SERVING.value, case_name, role, rendered)


def test_llm_serving_rendered_prompts_keep_required_domain_content():
    context = _CONTEXTS["full"]
    prompts = {role: _render_prompt(DomainName.LLM_SERVING, role, context) for role in _ROLES}
    normalized_prompts = {role: " ".join(prompt.split()) for role, prompt in prompts.items()}

    assert all("main.py" not in prompt for prompt in prompts.values())
    assert "pre-staged model weights" in prompts["implementer"]
    assert "serving-systems" in prompts["implementer"]
    assert "concurrent mixed-length correctness probe" in prompts["implementer"]
    assert "No machine-readable framework benchmark gate is declared" in prompts["judge"]
    assert "Benchmark sanity" not in prompts["judge"]
    assert "Accuracy checker — required to pass" not in prompts["judge"]
    assert "Reward-hack detection" in prompts["judge"]
    assert "changing that accounting cardinality" in prompts["judge"]
    assert "one model-delta record per generated model token" in prompts["implementer"]
    assert "streaming record granularity" in prompts["orchestrator"]
    assert "reward-hacking failure" in prompts["single_agent"]
    assert "decision-oriented coverage plan" in prompts["orchestrator"]
    assert "one missing scope at a time" in prompts["implementer"]
    assert "observer perturbation is material" in prompts["judge"]
    assert "perturbed capture is qualitative only" in prompts["single_agent"]
    assert "Scope discipline" in prompts["judge"]
    assert "For `disproven`" in prompts["judge"]
    assert "PASS for `supported` closes the scoped" in prompts["judge"]
    assert "configured framework gates" in prompts["judge"]
    assert "sparse official-evaluation policy" in prompts["judge"]
    assert "stronger diagnostic guard" in prompts["implementer"]
    assert "Classify invariant scope" in prompts["judge"]
    assert "retroactively erase a genuine" in prompts["orchestrator"]
    assert "restoration and no-regression gates one-sided" in prompts["orchestrator"]
    assert "parent-restoration and no-regression thresholds as one-sided" in prompts["implementer"]
    assert "restoration and no-regression gates one-sided" in prompts["judge"]
    assert "Parent-restoration and no-regression checks are one-sided" in prompts["single_agent"]
    assert "intermediate checkpoint" in prompts["implementer"]
    assert "not aspirational objective targets" in prompts["judge"]
    assert "cache/mask/position alignment" in prompts["judge"]
    assert "attention kernel itself must consume" in prompts["implementer"]
    assert "first reconstructs dense logical KV" in prompts["judge"]
    assert "allocator/layout experiment" in prompts["orchestrator"]
    assert "paged-attention compute path" in prompts["single_agent"]
    assert "Treat activation telemetry as part of the hot path" in prompts["implementer"]
    assert "observer-overhead invariant" in prompts["orchestrator"]
    assert "Audit observer overhead in activation telemetry" in prompts["judge"]
    assert "Treat telemetry as production hot-path code" in prompts["single_agent"]
    assert "Stage expensive evaluations" in prompts["implementer"]
    assert "add a cheap discriminating test or replay" in prompts["implementer"]
    assert "fails on the old implementation" in prompts["implementer"]
    assert "Reuse the established benchmark runner" in prompts["implementer"]
    assert "Treat new activation counters" in prompts["implementer"]
    assert "If this round creates or changes staged control flow" in prompts["implementer"]
    assert "Reuse established evaluation plumbing" in prompts["orchestrator"]
    assert "ordinary row data" in prompts["orchestrator"]
    assert "Require new controller code and its preflights only" in prompts["orchestrator"]
    assert "Expect ordinary candidate optimizations to reuse" in prompts["judge"]
    assert "do not by themselves justify controller edits" in prompts["judge"]
    assert "When the round creates or changes" in prompts["judge"]
    assert "Reuse the established benchmark runner" in prompts["single_agent"]
    assert "one hypothesis-agnostic extension" in prompts["single_agent"]
    assert "When you create or change staged control flow" in prompts["single_agent"]
    assert "distinguish a short" in prompts["implementer"]
    assert "exercise every new result" in prompts["implementer"]
    assert "preflight must exercise the newly changed" in prompts["implementer"]
    assert "minimum activation condition" in prompts["implementer"]
    assert "Match activation telemetry to when it is sampled" in prompts["implementer"]
    assert "Specify when each activation field is sampled" in prompts["orchestrator"]
    assert "temporal meaning of activation telemetry" in prompts["judge"]
    assert "Match activation telemetry to its sampling time" in prompts["single_agent"]
    assert "point-local" in prompts["implementer"]
    assert "point-local" in prompts["orchestrator"]
    assert "scope of every row's telemetry" in prompts["judge"]
    assert "structured response and domain-native artifact" in prompts["judge"]
    assert "separate retained exact-candidate artifact" in prompts["judge"]
    assert "local to each row" in prompts["single_agent"]
    assert "checkpoint each completed row" in prompts["implementer"]
    assert "Do not binary-search every integer concurrency" in prompts["implementer"]
    assert "raw response atomically" in prompts["implementer"]
    assert "raw response atomically" in prompts["single_agent"]
    assert "cache/mask/position alignment" in prompts["single_agent"]
    assert "not every integer" in prompts["orchestrator"]
    assert "refines a stable overload knee" in prompts["judge"]
    assert "initialized to zero but never mutated" in prompts["judge"]
    assert "field initialized to zero" in prompts["implementer"]
    assert "forecast, not a gate" in prompts["implementer"]
    assert "against the minimum acceptance criteria" in prompts["judge"]
    assert "not to grade implementations" in prompts["orchestrator"]
    assert "minimum-acceptance gate decides" in prompts["implementer"]
    assert "objective-level Pareto classification" in prompts["implementer"]
    assert "audit erroneous downgrades" in prompts["judge"]
    assert (
        "must not suppress a distinct objective-level frontier checkpoint"
        in prompts["orchestrator"]
    )
    assert "Retain a material improvement" in prompts["single_agent"]
    assert "describes a removed mechanism" in prompts["judge"]
    assert "independently computed FLOP/byte hardware ceiling" in prompts["judge"]
    assert "whole model step" in prompts["judge"]
    assert "whole model decode step" in prompts["orchestrator"]
    assert "collector source" in prompts["judge"]
    assert "buffered generation, not token streaming" in prompts["judge"]
    assert "smallest target-environment capability probe" in prompts["implementer"]
    assert "put it first in the same bounded controller invocation" in prompts["implementer"]
    assert (
        "A probe is not materially cheaper merely because it sends no workload"
        in prompts["implementer"]
    )
    assert "do not invent an ad hoc shorter cutoff" in prompts["implementer"]
    assert "do not poll a healthy" in prompts["implementer"]
    assert "back off up" in prompts["implementer"]
    assert "to 60 seconds while state is unchanged" in prompts["implementer"]
    assert "Apply the same rule on judge retries" in prompts["implementer"]
    assert "two `.remote()` calls is two accelerator startups" in prompts["implementer"]
    assert "small discrete bisection" in prompts["implementer"]
    assert "repair rounds after judge feedback" in prompts["orchestrator"]
    assert "issues two `.remote()` calls" in prompts["orchestrator"]
    assert "separate cold accelerator starts" in prompts["judge"]
    assert "issues two `.remote()` calls" in prompts["judge"]
    assert "self-review finds several target-hardware repairs" in prompts["single_agent"]
    assert "issues two `.remote()` calls" in prompts["single_agent"]
    assert "maximum paid workload invocations" in prompts["implementer"]
    assert "worst-case paid workload count" in prompts["judge"]
    assert "maximum paid workload invocations" in prompts["single_agent"]
    assert "maximum paid workload-invocation budget" in prompts["orchestrator"]
    assert "same candidate" in prompts["implementer"]
    assert "operating point as an already completed row" in prompts["implementer"]
    assert "Preserve already-valid measured rows" in prompts["implementer"]
    assert "Bind every representative or canonical performance claim" in prompts["implementer"]
    assert "convenient source file" in prompts["implementer"]
    assert "Audit candidate identity" in prompts["judge"]
    assert "primary-file hash" in prompts["judge"]
    assert "primary-file hash" in prompts["single_agent"]
    assert "retained benchmark variance" in prompts["implementer"]
    assert "Search retained diagnostic artifacts" in prompts["implementer"]
    assert "manufacture a fresh artifact" in prompts["implementer"]
    assert "Rollback can rewind" in prompts["implementer"]
    assert "Stage expensive evaluation behind a directional gate" in prompts["orchestrator"]
    assert "canonical-shape point" in prompts["orchestrator"]
    assert "prefer capability, smoke, and representative phases" in prompts["orchestrator"]
    assert "capability check which uses the same" in prompts["judge"]
    assert "separately launched runtime fingerprint" in prompts["judge"]
    assert "one-cold-start-per-variant" in prompts["judge"]
    assert "combine any capability" in prompts["single_agent"]
    assert "runtime fingerprint is another controller phase" in prompts["single_agent"]
    assert "reset-safe capability bisection" in prompts["single_agent"]
    assert "unrelated entry point is not useful" in prompts["orchestrator"]
    assert "minimal capability" in prompts["orchestrator"]
    assert "equivalent measurement from the same" in prompts["orchestrator"]
    assert 'runtime fingerprint or "cheap" probe' in prompts["orchestrator"]
    assert "small capability bisection" in prompts["orchestrator"]
    assert "Do not label a retry as a distinct capability hypothesis" in prompts["orchestrator"]
    assert "Do not repeat a previously disproven mechanism" in prompts["orchestrator"]
    assert "implementation language, runtime, process topology" in prompts["orchestrator"]
    assert "limits experimental uncertainty, not" in prompts["orchestrator"]
    assert "Implementation effort is a ranking" in prompts["orchestrator"]
    assert "not by an incumbent filename" in prompts["orchestrator"]
    assert (
        "trajectory as `advancing`, `noisy`, `plateauing`, or `regressing`"
        in prompts["orchestrator"]
    )
    assert "Infer a soft plateau" in prompts["orchestrator"]
    assert "at least three causally distinct alternatives" in prompts["orchestrator"]
    assert "incumbent implementation substrate is not an invariant" in prompts["implementer"]
    assert "does not require a small source diff" in prompts["implementer"]
    assert "rename, or reorganize incumbent implementation files" in prompts["implementer"]
    assert "disconnected micro-edits" in prompts["implementer"]
    assert "candidate components that use Python" in prompts["implementer"]
    assert "coordinated multi-component change" in prompts["judge"]
    assert "smallest causally complete production-path slice" in prompts["single_agent"]
    assert "fair terminal classification of this hypothesis" in prompts["implementer"]
    assert "runtime goal complete" in prompts["implementer"]
    assert "framework regains control" in prompts["implementer"]
    assert "framework control flow, not a place for optional future ideas" in prompts["implementer"]
    assert "Audit `next_step` as a lifecycle decision" in prompts["judge"]
    assert "Do not let a useful retained Pareto point" in prompts["judge"]
    assert "Live framework Pareto archive" in prompts["implementer"]
    assert "supersedes any numeric archive threshold" in normalized_prompts["implementer"]
    assert "recomputed from reviewed records" in prompts["judge"]
    assert "supersedes a numeric" in normalized_prompts["single_agent"]
    assert "recent attempts within this persistent hypothesis" in prompts["implementer"]
    assert "A distinguishing mechanism is necessary but not sufficient" in prompts["implementer"]
    assert "Amdahl-limited objective" in prompts["implementer"]
    assert "all consumers of that contract" in prompts["implementer"]
    assert "consumer-wide audit" in prompts["implementer"]
    assert "building a FastAPI inference server" not in prompts["implementer"]
    assert "Your `main.py` must" not in prompts["implementer"]
    assert "not the incumbent" in prompts["judge"]
    assert "bounded queues and backpressure" in prompts["judge"]
    assert "Do NOT modify `main.py`" not in prompts["judge"]
    assert "typically `main.py`" not in prompts["orchestrator"]
    assert "Do not require a duplicate benchmark" in prompts["orchestrator"]
    assert "Make performance gates variance-aware" in prompts["orchestrator"]
    assert "performance-modeling.md" in prompts["orchestrator"]
    assert "current-architecture ceiling" in prompts["orchestrator"]
    assert "multiplicative gap" in prompts["orchestrator"]
    assert "implied post-change metric range" in prompts["orchestrator"]
    assert "terminally sufficient on its own" in prompts["orchestrator"]
    assert "one jointly attainable operating point" in normalized_prompts["orchestrator"]
    assert "optimistic latency bound still misses" in normalized_prompts["orchestrator"]
    assert "information gained per" in prompts["orchestrator"]
    assert "accelerator minute" in prompts["orchestrator"]
    assert "reference engine's observed score" in prompts["orchestrator"]
    assert "required cycle time at" in prompts["orchestrator"]
    assert "minimum useful tokens per step" in prompts["orchestrator"]
    assert "queued concurrency is not useful batch work" in prompts["orchestrator"]
    assert "retain a named roadmap item" in prompts["orchestrator"]
    assert "bound the detour to one decision" in prompts["orchestrator"]
    assert "do not let yourself cheat" in prompts["single_agent"]
    assert "Evidence-led optimization method" in prompts["orchestrator"]
    assert "Continuous batching" not in prompts["orchestrator"]
    assert "CUDA graphs" not in prompts["orchestrator"]


def test_orchestrator_rejects_quantitative_use_of_perturbed_profiles():
    context = _CONTEXTS["full"]
    rendered = render_template(
        "orchestrator_plan_prompt.j2",
        template_dir=_TEMPLATE_DIR,
        objective=context["objective"],
        profiler_summary={
            "bottlenecks": "sampling looks expensive",
            "suggestions": "optimize sampling",
            "analysis": "profiled throughput is lower than control",
            "perf_metric": None,
            "perf_unit": None,
        },
        regression_info=None,
        exhaustion_info=None,
        roadmap_text=context["roadmap_text"],
        recent_progress_text=context["recent_progress_text"],
        progress_location=context["progress_location"],
        roadmap_location=context["roadmap_location"],
        plateau_warning=None,
        runtime_notes=context["runtime_notes"],
        env_kind=context["env_kind"],
        domain_orchestrator=_domain_section(DomainName.LLM_SERVING, "orchestrator", context),
        official_eval_every=3,
        provisional_candidates=0,
        official_eval_cadence_due=False,
    )

    normalized = " ".join(rendered.split())
    assert "profile changed the headline metric by more than 10%" in normalized
    assert "Do not turn it into exclusive phase shares" in normalized
    assert "causal A/B experiment instead" in normalized


def test_pre_round_prompt_does_not_profile_a_future_rollback_target():
    rendered = render_template(
        "orchestrator_pre_round_prompt.j2",
        template_dir=_TEMPLATE_DIR,
        objective="maximize throughput",
        regression_info="A disproven hypothesis remains in the workspace.",
        exhaustion_info=None,
        progress_location="progress/",
        recent_progress_text="# Round 4\n\nOutcome: disproven",
    )

    assert "profiling phase runs before" in rendered
    assert "let the plan restore the trusted" in rendered


def test_official_evaluation_due_changes_agent_measurement_contract():
    context = {
        **_CONTEXTS["full"],
        "official_evaluation_due": True,
        "official_evaluation_reason": "cadence",
        "framework_benchmark_enabled": False,
        "implementer_outcome": "nominated",
        "implementer_perf_metric": None,
        "gate_revalidation_pending": False,
    }

    implementer = _render_prompt(DomainName.LLM_SERVING, "implementer", context)
    judge = _render_prompt(DomainName.LLM_SERVING, "judge", context)

    assert "An official evaluation is due" in implementer
    assert "run one canonical benchmark" in implementer
    assert "provide one fresh canonical metric" in judge


def test_minimal_llm_serving_prompt_omits_optional_checker_paths():
    context = _CONTEXTS["minimal"]
    judge = _render_prompt(DomainName.LLM_SERVING, "judge", context)
    single_agent = _render_prompt(DomainName.LLM_SERVING, "single_agent", context)

    assert "/workspace/bench/benchmark.py" not in judge
    assert "/workspace/acc_checker/checker.py" not in judge
    assert "/workspace/bench/benchmark.py" not in single_agent
    assert "/workspace/acc_checker/checker.py" not in single_agent


def test_generic_prompts_do_not_receive_llm_serving_domain_content():
    context = _CONTEXTS["full"]
    prompts = {role: _render_prompt(DomainName.GENERIC, role, context) for role in _ROLES}

    assert "Model weights are at `/model`" not in prompts["implementer"]
    assert "Use references as implementation support" not in prompts["implementer"]
    assert "Benchmark sanity" not in prompts["judge"]
    assert "Reward-hack detection" not in prompts["judge"]
    assert "do not let yourself cheat" not in prompts["single_agent"]
    assert "Evidence-led optimization method" not in prompts["orchestrator"]
