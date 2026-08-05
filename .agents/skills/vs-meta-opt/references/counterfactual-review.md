# Counterfactual Trajectory Review

## Purpose

Use independent subagents to test whether VibeSys is generating strong next steps, then diagnose the agent-system mechanism behind any meaningful proposal gap. This is a selective plateau or uncertainty tool, not a mandatory per-round judge.

## Trigger Selectively

Consider a review when:

- The trusted frontier or learning trajectory plateaus.
- Consecutive rounds repeat similar low-impact directions.
- VibeSys continues after a hypothesis appears falsified or exhausted.
- The measured gap lacks a convincing causal or performance model.
- The next plan appears weak, overly narrow, or disconnected from recent evidence.
- VS Meta Opt is genuinely unsure whether the proposed continuation is reasonable.

Skip it when the next step is already decisive, the validation window is incomplete, or the remaining token/time budget cannot change the decision.

## Preserve Independence

Snapshot VibeSys's proposed next step, but do not show it to reviewers initially. Spawn one to three fresh subagents according to uncertainty, stakes, and budget. Give them only legitimate campaign context:

- Objective, constraints, and evaluation contract.
- Current trusted frontier and remaining performance gap.
- Paths to recent round summaries, raw measurements, profiles, roadmap, and relevant candidate diffs.
- Remaining experiment budget and prohibited shortcuts.

Do not provide the intended critique, suspected missing optimization, prior winning answer, hidden evaluator behavior, or another reviewer's conclusion. Prefer artifact paths over pasted history.

Ask each reviewer to return:

```text
Observed bottleneck or uncertainty:
Evidence paths:
Best next hypothesis or discriminating measurement:
Expected impact and limiting ceiling:
Activation evidence and falsifier:
Cost and validation window:
Important alternatives or missing evidence:
```

Use distinct reasoning lenses only when useful, such as performance modeling, system-architecture breadth, or experimental design. Do not encode a candidate answer in the lens.

## Compare Without Voting

After collecting independent responses, reveal VibeSys's recorded proposal and compare all proposals on:

- Fit to evidence available at the decision time.
- Explicit causal and performance model.
- Plausible impact relative to the remaining gap.
- Falsifiability and activation evidence.
- Experiment cost and information value.
- Correctness, integrity, and reward-hacking risk.
- Novelty relative to attempts already made.
- Compatibility with remaining budget.

Do not select by majority vote or rhetorical confidence. The subagents are counterfactual consultants, not ground truth. Record when VibeSys's proposal is equally strong or stronger.

## Diagnose the Proposal Gap

If an independent alternative is materially stronger, explain why VibeSys did not generate or select it. Map the earliest supported cause to the failure taxonomy. Possibilities include:

- Missing or weak performance modeling.
- Too many planning, implementation, evaluation, or synthesis responsibilities in one role.
- Relevant evidence or trajectory summaries were unavailable, stale, or too expensive to inspect.
- The right reusable skill was missing, not discoverable, or not selected.
- Prompt or incumbent context anchored the search too narrowly.
- Continuation, evaluation, or reward policy favored low-risk local work.
- Agent context or inference budget was insufficient for the decision.
- `Other`: a material mechanism not captured above.

Distinguish a systematic VibeSys weakness from a reasonable stochastic miss. One surprising alternative rarely justifies global policy by itself.

## Improve the Generator, Not the Answer

Form a VibeSys-level intervention that makes future agents more likely to produce or select strong directions. Examples of intervention surfaces include:

- Require an explicit gap or ceiling model at selected decision points.
- Narrow or split overloaded role responsibilities.
- Improve path-based evidence summaries or retrieval.
- Add or improve a reusable, versioned, selectively loaded skill.
- Adjust hypothesis leases, search diversity, or comparison criteria.
- Add instrumentation that distinguishes competing system models.

Do not insert the winning candidate proposal, discovered bottleneck, or benchmark-specific recipe into an always-on prompt. Preserve the counterfactual proposals as campaign artifacts and validate the system intervention on later unseen decisions.

## Track Cost and Outcome

Record the trigger, reviewers, artifact inputs, proposal summaries, comparison, attributed failure, system response, and token/time cost in the draft PR. Later record whether VibeSys independently generated better directions after the intervention.

Stop spawning reviewers when additional perspectives repeat existing reasoning or cannot affect the next meta-decision.
