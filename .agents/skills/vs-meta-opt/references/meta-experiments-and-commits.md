# VS Meta Opt Runs and Clean Commits

## Contents

- Keep One Draft PR Per Run
- Define the Meta-Hypothesis
- Select a Validation Design
- Perform a Leakage Review
- Require a Clean Change Boundary
- Use the Rationale Commit Template
- Decide the Outcome Independently

## Keep One Draft PR Per Run

Define a meta-optimization run as one bounded campaign that may evaluate multiple sequential VibeSys system hypotheses over one or more named audit windows. Do not open a PR for every candidate round, hypothesis, or commit. At the start of each run:

1. Create a dedicated branch from the recorded VibeSys parent.
2. Open a draft PR using `assets/meta-run-pr-template.md` before modifying behavior.
3. Fill the audit scope and baseline effectiveness from artifact paths.
4. Keep the PR description current as the run's concise effectiveness ledger.

Update the description after each hypothesis, intervention commit, revert, and validation checkpoint. Track measurements, confounders, and disposition changes without replacing prior observations silently. Raw logs and campaign history remain in artifacts; the PR links them.

At the end, mark the PR ready only when its retained set of changes is validated enough for review. Preserve failed experiments with explicit revert commits and ledger entries instead of rewriting the trajectory. Close the draft with a recorded `No change`, `Revert`, or `Inconclusive` disposition when there is no intervention worth merging.

## Define the Meta-Hypothesis

Use this template before changing VibeSys:

```text
Audit boundary:
Observed VibeSys behavior:
Evidence paths:
Failure category, including Other if needed:
Causal agent-system mechanism:
Intervention surface:
Proposed VibeSys change:
Why this improves discovery rather than leaking an answer:
Expected meta-metric effects:
Possible regressions and confounders:
Validation window or control:
Success criteria:
Reversion condition:
Generality claim:
```

State uncertainty explicitly. If instrumentation is inadequate, make instrumentation the first intervention rather than changing behavior on weak evidence.

## Select a Validation Design

Prefer, in descending strength when practical:

1. Comparable campaigns with and without the system change.
2. Alternating or randomized policy assignment across independent hypotheses.
3. Before/after windows with stable objective, evaluator, environment, and opportunity.
4. Replay or simulation for deterministic coordination and lifecycle behavior.
5. A prospective window with predeclared thresholds when no valid control exists.

Measure both intended effects and likely regressions. Examples include frontier velocity, useful-information yield, pivot latency, duplicate evaluations, agent tokens, phase time, cost, infrastructure failures, and invalid dispositions.

Do not claim causality from a single candidate improvement when candidate difficulty, stochasticity, or prior knowledge could explain it.

## Perform a Leakage Review

Before landing the change, inspect every modified prompt and default context source:

- Does it name or imply a candidate optimization learned from the audited run?
- Does it expose a benchmark quirk, winning architecture, hidden evaluator detail, or prior answer?
- Could the same procedural instruction be stated without domain-specific content?
- Should reusable domain knowledge live in a selectively loaded skill instead?
- Is the skill set and version recorded so its effect can be attributed honestly?

Reject direct prompt leakage. A skill addition or improvement is allowed when it is an explicit, versioned VibeSys capability change.

## Require a Clean Change Boundary

Before editing:

- Start from the intended VibeSys parent with a clean worktree.
- Read repository instructions and nearby implementations that establish the relevant conventions and ownership boundaries.
- Preserve unrelated user changes and campaign artifacts.
- Record the parent commit in the meta-experiment.

While editing:

- Keep one causal agent-system intervention per commit when it can be isolated.
- Treat a canonical schema change, its one-way migration of existing generated state, and its migration tests as one atomic intervention rather than separate compatibility features.
- Do not mix candidate implementation changes, benchmark outputs, or run state with VibeSys changes.
- Scope the intervention by one causal responsibility, not by the fewest files or lines.
- Reuse or extend an existing abstraction when it owns the behavior; introduce one when it creates a genuine ownership boundary, improves data flow, or removes meaningful duplication.
- Refactor adjacent code when needed for an ergonomic, maintainable implementation, but leave unrelated cleanup out of the commit.
- Reject benchmark-specific branches, scattered special cases, and compatibility shims used to avoid a proper design.
- Include prompt snapshots when rendered prompt behavior changes.
- Add focused tests sufficient to cover the claimed behavior, important failure cases, and any new abstraction boundary.

Before committing:

- Review the complete diff for scope and leakage.
- Confirm the result follows project conventions and reads as a natural extension of the architecture rather than a patch around it.
- Run targeted validation and repository-required formatting checks.
- Verify the worktree contains only intentional files.
- Make the commit independently reviewable and revertible.
- For schema changes, verify all live state migrates and ordinary runtime paths no longer implement the old schema.

## Use the Rationale Commit Template

Use an imperative subject describing the VibeSys behavior change. Include this body:

```text
<Imperative VibeSys system change>

Meta-finding:
<Observed deficiency in VibeSys, not the candidate.>

Evidence:
<Artifact paths, rounds, and measurements supporting the finding.>

Rationale:
<Causal mechanism and why this intervention is appropriate.>

Design:
<Project conventions followed; abstractions reused, extended, introduced, or
refactored; and why the resulting structure is ergonomic and maintainable.>

Expected effect:
<Predeclared meta-metrics and direction.>

Scope and leakage:
<Why the change is general, what it intentionally excludes, and confirmation
that prompts do not encode a candidate optimization or benchmark answer.>

Validation:
<Tests already run plus the prospective campaign/control needed.>

Risks and reversion:
<Possible regressions, confounders, and the condition for reverting.>
```

Use `Not applicable` only when a field genuinely does not apply; do not omit the rationale fields. Keep paths concise and repository-relative where possible.

After committing:

- Record the commit hash, parent, and validation policy with the campaign audit.
- Keep the worktree clean.
- Do not amend the evidence after observing the validation outcome; add a follow-up record.

## Decide the Outcome Independently

The author may propose the change, but later framework-owned evidence determines disposition:

- **Retain:** intended improvement appears with acceptable regressions.
- **Revise:** mechanism remains plausible but the implementation or scope was incomplete.
- **Revert:** success criteria fail or regressions exceed the predeclared bound.
- **Inconclusive:** evidence cannot distinguish the change from confounders; gather the named missing evidence.

Do not silently retain an inconclusive system change because it sounds reasonable.
