---
name: vs-meta-opt
description: Audit how effectively VibeSys conducts optimization campaigns and propose evidence-backed improvements to its agent roles, prompts, skills, search policy, evaluation, sessions, evidence flow, and framework lifecycle. Use when reviewing recent rounds, explaining slow or stalled convergence, separating candidate failures from agent-system failures, diagnosing cost, context, coordination, or evaluation waste, or preparing and validating a rationale-bearing VibeSys system change.
---

# VS Meta Opt

## Purpose

Judge and improve the optimizer, not the bespoke system it produces. Determine whether VibeSys used the evidence and opportunities available at the time effectively, identify the limiting agent-system mechanism, and propose a testable change to VibeSys.

Preserve a strict boundary:

- Treat candidate implementation choices and domain performance techniques as inner-loop work.
- Treat role behavior, search policy, prompts, skill availability, evidence, evaluation, context, lifecycle, and cost control as meta-optimization surfaces.
- Do not become a shadow candidate designer.

## Read Only What Is Relevant

- Read [effectiveness-rubric.md](references/effectiveness-rubric.md) to audit rounds, a trajectory window, or multiple campaigns.
- Read [diagnosis-and-levers.md](references/diagnosis-and-levers.md) to attribute a deficiency, choose a VibeSys control lever, or check for prompt leakage.
- Read [meta-experiments-and-commits.md](references/meta-experiments-and-commits.md) before changing VibeSys or validating whether a system change helped.
- Use [meta-run-pr-template.md](assets/meta-run-pr-template.md) to open and maintain the required draft PR for each bounded meta-optimization run.

Read campaign state, history, prompts, diffs, logs, profiles, timings, costs, and evaluations from artifact paths. Do not embed durable history or raw evidence into agent prompts.

## Audit Workflow

### 1. Open the run draft PR

Treat one run as one bounded meta-optimization campaign that may test multiple sequential meta-hypotheses over one or more audit windows. It is not one candidate round, hypothesis, or commit. Start from the intended VibeSys parent on a dedicated clean branch and immediately open one draft PR using [meta-run-pr-template.md](assets/meta-run-pr-template.md).

Use the PR description as the living effectiveness ledger. Update it after the initial audit, every intervention commit, each validation checkpoint, and the final retain, revise, revert, or inconclusive decision. Link artifacts rather than pasting raw logs.

### 2. Define the audit boundary

Record:

- Campaign, round range, objective, and environment.
- VibeSys commit and versions of prompts, skills, evaluators, and policy.
- Trusted candidate frontier at the start and end.
- Available phase timing, token, cost, failure, and evaluation artifacts.
- Important missing instrumentation.

Do not compare windows governed by different objectives or measurement semantics without identifying the confounder.

### 3. Reconstruct the trajectory

Build a concise chronology from raw artifacts rather than role self-reports. For each round, recover:

- The evidence available before the decision.
- The chosen hypothesis and expected information.
- Candidate and VibeSys changes, kept distinct.
- Focused and official evidence actually produced.
- Disposition, cost, elapsed time, and next decision.

Treat observations, agent claims, and auditor inferences as separate fields.

### 4. Judge at three timescales

- **Round:** Was the decision reasonable given evidence available then, regardless of outcome?
- **Window:** Did recent rounds form a coherent learning trajectory with timely pivots and little duplicated work?
- **Campaign/system:** Does the pattern recur strongly enough across workloads or environments to justify reusable VibeSys policy?

Avoid outcome and hindsight bias. A regression may decisively falsify a plausible path; a lucky improvement may conceal an inefficient process.

### 5. Classify effectiveness

Use the rubric categories:

- Outcome effectiveness.
- Learning effectiveness.
- Operational efficiency.
- Coordination quality.
- Evidence integrity.
- Search quality and generality.
- **Other:** Record material findings that do not fit cleanly. Explain why existing categories are inadequate rather than force-fitting them.

Use quantitative measurements where available and concrete artifact paths everywhere. Scores may summarize evidence, but never replace findings.

### 6. Attribute the limiting system mechanism

Distinguish candidate, implementation, activation, evaluation, infrastructure, coordination, context, search-policy, incentive, knowledge, and `Other` failures. Identify the narrowest VibeSys-owned mechanism supported by evidence.

If the evidence cannot distinguish candidate difficulty from agent-system weakness, recommend instrumentation or a discriminating meta-experiment rather than a speculative fix.

### 7. Propose the next meta-level hypothesis

State:

- The observed VibeSys behavior and artifact evidence.
- The causal agent-system mechanism.
- The smallest prompt, skill, policy, role, framework, or instrumentation intervention that tests it.
- Expected effects on frontier velocity, learning yield, time, cost, or integrity.
- Likely regressions and cross-domain risks.
- Validation window, success criteria, and reversion condition.

Test meta-hypotheses sequentially within the run. Prefer one causal system change per experiment and one independently understandable commit per intervention. Keep campaign-local adjustments separate from reusable framework policy.

### 8. Enforce the no-leakage boundary

Keep prompts procedural and neutral. Prompts may define roles, decision procedures, evidence requirements, interfaces, constraints, and how to discover relevant skills. Do not encode a candidate optimization, known bottleneck, benchmark-specific trick, prior winning implementation, or hidden evaluator behavior directly into prompts.

Adding or improving skills available to VibeSys is allowed. Keep skills modular, versioned, selectively loaded, and distinguish their contribution from orchestration changes. Pass skill names or paths; do not paste their contents into prompts.

### 9. Land a clean, rationale-bearing commit

Before modifying VibeSys, read [meta-experiments-and-commits.md](references/meta-experiments-and-commits.md). Keep the worktree clean, separate agent-system changes from candidate code and campaign artifacts, validate the narrow behavior, and create a reviewable commit for each intervention using the required rationale template.

Record every resulting commit hash and disposition in the run PR so later audits know exactly which VibeSys behavior governed each round.

### 10. Validate independently

Do not certify an intervention because its author expects it to work. Predeclare meta-metrics and compare later framework-owned evidence against the prior window or another suitable control. Separate improved candidate luck from improved VibeSys behavior.

Retain, revise, or revert the system change based on the predeclared evidence. Promote it to general policy only when its scope and transferability justify that conclusion. Keep the draft PR description current; mark it ready only when the intervention is justified and reviewable. If no change is justified, record that result and close the draft rather than merging an empty intervention.

## Guardrails

- Do not propose first-order candidate optimizations as the audit result.
- Do not put discovered optimizations or tricks into always-on agent prompts.
- Do not judge VibeSys only by the final candidate score.
- Do not force every finding into a closed taxonomy; use `Other` with rationale.
- Do not infer causality from one successful round without considering opportunity, cost, and confounders.
- Do not mix a VibeSys system intervention with candidate changes in one commit.
- Do not let agent prose override immutable framework evidence.
- Do not turn an auditor recommendation into policy without a validation and reversion plan.

## Expected Handoff

Return:

1. Audit boundary and evidence paths.
2. Effectiveness findings by category, including `Other` when applicable.
3. Limiting VibeSys mechanism and confidence.
4. The next meta-level intervention with owner and leakage assessment.
5. Expected meta-metric effects, validation window, and reversion condition.
6. Current run commit log and the next clean commit plan using the required rationale template.
7. Missing evidence and unresolved confounders.
