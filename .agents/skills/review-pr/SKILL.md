---
name: review-pr
description: Review VibeSys pull requests and local diffs for correctness, regressions, architecture violations, insufficient tests, contract or documentation drift, and risky prompt changes. Use when a user asks to review, audit, assess, or find bugs in a VibeSys PR, branch, commit, patch, or working-tree change, including pre-merge reviews and second-pass reviews after updates.
---

# Review PR

## Overview

Produce a high-signal review of the change, not a general code tour. Prioritize
specific defects introduced by the diff and verify each finding against the
repository's contracts, architecture, tests, and affected callers.

## Workflow

1. Resolve the review target and intent.
   - Read `AGENTS.md`, `docs/coding-best-practices.md`, and every applicable
     subtree instruction before judging the change.
   - Inspect repository status before switching branches or fetching a PR.
     Preserve user changes and prefer reviewing without mutating the worktree.
   - Read the PR title, body, linked issue, base/head commits, changed files,
     existing review threads, and check results when available. Use the local
     branch or merge-base diff when reviewing unpublished work.
   - Derive the intended behavior and correctness properties. Call out material
     ambiguity instead of silently choosing an interpretation.
   - Do not submit a GitHub review, comment, approval, or change request unless
     the user explicitly asks for that external write.

2. Build a change map before evaluating individual lines.
   - Classify changed files by owner: framework, reusable library, client,
     evaluator, example bundle, prompt/template/skill, configuration/schema,
     generated artifact, test, or documentation.
   - Read enough surrounding implementation and history to understand the
     existing pattern. Trace changed interfaces to their producers, consumers,
     serialization boundaries, and tests with `rg`.
   - Identify authoritative sources and generated outputs. Review generated
     diffs as behavior, but request fixes in the source of truth.
   - Look for coupled artifacts that should have changed but did not: tests,
     snapshots, schemas, generated types, package data, examples, contracts,
     migration or compatibility code, and user-facing documentation.

3. Apply the review guide.
   - Read [references/review-guide.md](references/review-guide.md) completely.
   - Use only the language and surface sections relevant to the diff, plus the
     cross-cutting checks.
   - Review new code placement explicitly. Favor an existing canonical owner;
     extract a standalone reusable capability into `libs/` only when it has a
     real interface and reuse boundary. Flag both misplaced shared behavior and
     abstractions that add indirection without protecting a boundary.

4. Verify candidate findings.
   - Confirm that the reviewed change introduced or exposed the problem.
   - State the concrete input, state, platform, or execution path that triggers
     it and the observable consequence.
   - Check nearby tests and run the narrowest useful read-only test or
     reproduction when practical. Never claim a command ran when it did not.
   - Reject findings based only on taste, hypothetical future needs, or a
     preference already enforced automatically unless there is a material
     correctness or maintenance consequence.
   - Re-read the final diff and each cited line. Distinguish a proven defect
     from a residual risk that needs more evidence.

5. Report the review.
   - Lead with findings ordered by severity. Use `[P0]` through `[P3]`, a short
     actionable title, one compact explanation, and the tightest changed-line
     range that demonstrates the issue.
   - Explain why the behavior is wrong and when it occurs; include a fix
     direction only when it clarifies the required contract. Do not write a
     patch unless the user also asks for implementation.
   - Keep summaries brief. After findings, list assumptions or open questions,
     checks run, and residual risks only when they add information.
   - If there are no actionable findings, say so plainly and mention meaningful
     verification gaps. Do not invent low-value findings to populate a review.

## Severity

- `P0`: Release-blocking or broadly catastrophic; immediate action is required.
- `P1`: A serious, likely defect that should block merge.
- `P2`: A real defect with limited scope or a meaningful missing safeguard.
- `P3`: A small but concrete correctness or maintainability problem worth fixing.

Severity reflects impact, likelihood, and blast radius rather than diff size.

## Review Boundaries

- Treat tests, docs, prompts, templates, skills, schemas, and manifests as
  product behavior when users or agents depend on them.
- Focus on changed behavior. Mention pre-existing defects separately and only
  when they materially affect whether this change is safe.
- Do not expose credentials, private logs, or sensitive paths in review output.
- Do not approve merely because checks pass; tests demonstrate selected
  properties, not the absence of regressions.
