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
    "invariants": "Accuracy and prompt-dependent generation remain unchanged.",
    "implementer_outcome": "nominated",
    "implementer_evidence": "Replay counter increased in a targeted probe.",
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
            invariants=context["invariants"],
            progress_location=context["progress_location"],
            domain_implementer=_domain_section(domain, "implementer", context),
            framework_benchmark_enabled=context.get("framework_benchmark_enabled", False),
            official_evaluation_due=context.get("official_evaluation_due", False),
            official_evaluation_reason=context.get("official_evaluation_reason"),
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
            invariants=context["invariants"],
            implementer_outcome=context["implementer_outcome"],
            implementer_evidence=context["implementer_evidence"],
            implementer_perf_metric=context.get("implementer_perf_metric"),
            gate_revalidation_pending=context.get("gate_revalidation_pending", False),
            progress_location=context["progress_location"],
            framework_benchmark_enabled=context.get("framework_benchmark_enabled", False),
            official_evaluation_due=context.get("official_evaluation_due", False),
            official_evaluation_reason=context.get("official_evaluation_reason"),
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
            invariants=context["invariants"],
            progress_location=context["progress_location"],
            framework_benchmark_enabled=context.get("framework_benchmark_enabled", False),
            official_evaluation_due=context.get("official_evaluation_due", False),
            official_evaluation_reason=context.get("official_evaluation_reason"),
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

    assert "pre-staged model weights" in prompts["implementer"]
    assert "serving-systems" in prompts["implementer"]
    assert "concurrent mixed-length correctness probe" in prompts["implementer"]
    assert "No machine-readable framework benchmark gate is declared" in prompts["judge"]
    assert "Benchmark sanity" not in prompts["judge"]
    assert "Accuracy checker — required to pass" not in prompts["judge"]
    assert "Reward-hack detection" in prompts["judge"]
    assert "Scope discipline" in prompts["judge"]
    assert "For `disproven`" in prompts["judge"]
    assert "PASS for `supported` closes the scoped" in prompts["judge"]
    assert "configured framework gates" in prompts["judge"]
    assert "sparse official-evaluation policy" in prompts["judge"]
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
    assert "Make staged controllers fail closed in code" in prompts["implementer"]
    assert "Exercise the successful post-measurement path too" in prompts["implementer"]
    assert "Require the controller itself to fail closed" in prompts["orchestrator"]
    assert "synthetic successful-row preflight" in prompts["orchestrator"]
    assert "Inspect staged-controller control flow" in prompts["judge"]
    assert "synthetic successful-row preflight" in prompts["judge"]
    assert "Make the controller fail closed in code" in prompts["single_agent"]
    assert "Preflight the success path with a fake representative row" in prompts["single_agent"]
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
    assert "raw response atomically" in prompts["implementer"]
    assert "raw response atomically" in prompts["single_agent"]
    assert "cache/mask/position alignment" in prompts["single_agent"]
    assert "not every integer" in prompts["orchestrator"]
    assert "describes a removed mechanism" in prompts["judge"]
    assert "independently computed FLOP/byte hardware ceiling" in prompts["judge"]
    assert "buffered generation, not token streaming" in prompts["judge"]
    assert "smallest target-environment capability probe" in prompts["implementer"]
    assert "put it first in the same bounded controller invocation" in prompts["implementer"]
    assert "Apply the same rule on judge retries" in prompts["implementer"]
    assert "repair rounds after judge feedback" in prompts["orchestrator"]
    assert "separate cold accelerator starts" in prompts["judge"]
    assert "self-review finds several target-hardware repairs" in prompts["single_agent"]
    assert "Preserve already-valid measured rows" in prompts["implementer"]
    assert "retained benchmark variance" in prompts["implementer"]
    assert "Search retained diagnostic artifacts" in prompts["implementer"]
    assert "manufacture a fresh artifact" in prompts["implementer"]
    assert "Rollback can rewind" in prompts["implementer"]
    assert "Stage expensive evaluation behind a directional gate" in prompts["orchestrator"]
    assert "canonical-shape point" in prompts["orchestrator"]
    assert "prefer capability, smoke, and representative phases" in prompts["orchestrator"]
    assert "capability check which uses the same" in prompts["judge"]
    assert "combine any capability" in prompts["single_agent"]
    assert "unrelated entry point is not useful" in prompts["orchestrator"]
    assert "minimal capability" in prompts["orchestrator"]
    assert "equivalent measurement from the same" in prompts["orchestrator"]
    assert "Do not label a retry as a distinct capability hypothesis" in prompts["orchestrator"]
    assert "Do not repeat a previously disproven mechanism" in prompts["orchestrator"]
    assert "Do not require a duplicate benchmark" in prompts["orchestrator"]
    assert "Make performance gates variance-aware" in prompts["orchestrator"]
    assert "performance-modeling.md" in prompts["orchestrator"]
    assert "current-architecture ceiling" in prompts["orchestrator"]
    assert "multiplicative gap" in prompts["orchestrator"]
    assert "reference engine's observed score" in prompts["orchestrator"]
    assert "do not let yourself cheat" in prompts["single_agent"]
    assert "Evidence-led optimization method" in prompts["orchestrator"]
    assert "Continuous batching" not in prompts["orchestrator"]
    assert "CUDA graphs" not in prompts["orchestrator"]


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
