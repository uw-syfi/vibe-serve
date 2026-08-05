# Long-Running VS Meta Opt Control

## Contents

- Establish the Run
- Launch and Segment the Campaign
- Monitor with Adaptive Heartbeats
- Check Every Completed Round
- Run Counterfactual Reviews Selectively
- Pause, Modify, and Resume
- Enforce Budgets
- Handle Failures and Finish

## Establish the Run

Inspect the specified VibeSys input and repository instructions rather than assuming a fixed CLI shape. Record the exact launch/resume command, working directory, environment, input revisions, VibeSys parent, campaign identity, and checkpoint in the draft PR.

Set hard limits for:

- Candidate rounds.
- Wall-clock time.
- Accelerator time or monetary cost when relevant.
- Agent turns or inference budget when relevant.
- Meta-interventions or commits.
- Consecutive infrastructure failures.

Reserve enough budget for terminal official evaluation, evidence publication, and deterministic cleanup. Recalculate remaining budget after every round, commit, retry, and paid evaluation.

## Launch and Segment the Campaign

Run VibeSys in a persistent execution session whose output remains available without placing the full transcript in agent context. Keep complete logs and artifacts on disk.

Treat a continuous period governed by one VibeSys commit as a campaign segment. For every segment, record:

- VibeSys commit and configuration.
- Start checkpoint and round.
- Exact command and input identity.
- Active meta-hypothesis, if any.
- End checkpoint, reason, and evidence paths.

Keep the source tree consumed by the live process stable. VS Meta Opt may develop and commit changes concurrently in a separate clean worktree. Activate them only by starting or resuming a new segment pinned to the new commit.

## Monitor with Adaptive Heartbeats

Pick each sleep interval from current context. Consider the active phase, recent heartbeat cadence, expected completion time, deadline proximity, failure risk, remote-resource cost, and whether a user-visible update is due.

Use shorter sleeps during startup, transitions, warnings, or near deadlines. Use longer sleeps while a healthy agent turn, build, deployment, or evaluation is expected to take time. There is no useful fixed interval for every phase. Avoid repeated checks that cannot change the next decision.

On wakeup:

1. Check process liveness and current phase.
2. Read only data after the saved event, byte, line, or round cursor.
3. Update last-progress time and consumed budgets.
4. Detect a round completion, warning, deadline, failure, or terminal state.
5. Choose and record the next wake reason and interval.

If nothing changed and progress remains within phase expectations, return to sleep without rereading history or invoking a deep audit.

Keep a compact monitor checkpoint:

```text
PR and campaign:
VibeSys commit and segment:
Last completed round:
Current phase:
Last progress timestamp:
Event/log cursor:
Active meta-hypothesis:
Validation window remaining:
Round/time/cost/intervention budgets remaining:
Next wake reason and interval:
```

## Check Every Completed Round

Never advance past a completed round without a lightweight trajectory assessment. Read the new round summary and only the raw artifacts needed to verify uncertain claims.

Classify the round as one or more of:

- Frontier progress.
- Valid new information.
- Expected continuation toward a decisive result.
- Inconclusive or within noise.
- Repeated known information.
- Evaluation, infrastructure, coordination, context, integrity, or `Other` concern.

Record the classification, evidence path, remaining budget, and whether to continue, deepen the audit, or pause. A healthy classification should be cheap and should not trigger an intervention automatically.

Trigger deeper analysis when:

- The same warning appears in consecutive rounds.
- The trusted trajectory plateaus or regresses without learning value.
- A hypothesis continues after its falsifier or lease.
- Evaluation is duplicated, invalid, or disproportionately expensive.
- Agent context, infrastructure, retries, or recovery dominate useful work.
- Integrity, candidate provenance, or reward-hacking risk is uncertain.
- A meta-intervention reaches its validation checkpoint.

## Run Counterfactual Reviews Selectively

When a plateau or uncertain next step makes an independent comparison valuable, follow `references/counterfactual-review.md`. Do not invoke reviewers merely because a round completed.

First preserve VibeSys's next proposal. Then give fresh subagents raw artifact paths without that proposal, collect their alternatives, and compare only after independence is established. Charge their time and tokens to the run budget.

Use the comparison to diagnose VibeSys. A stronger independent proposal is evidence of a possible modeling, role-scope, context, skill-routing, search-policy, incentive, knowledge, or `Other` failure—not permission to hardcode that proposal into future prompts.

## Pause, Modify, and Resume

At a safe activation boundary, after any parallel development in a separate worktree:

1. Save campaign and candidate checkpoints.
2. Terminate or pause the VibeSys process without leaking owned resources.
3. Record the ending segment and exact framework commit.
4. Form one VibeSys-level hypothesis from the audit evidence.
5. Implement and test one causal intervention.
6. Commit it with the rationale template and update the draft PR.
7. Resume the same campaign as a new segment governed by the new commit.
8. Observe the predeclared validation window, then retain, revise, or revert.

If a change alters measurement semantics, persisted state, or compatibility, do not pretend the windows are directly comparable. Migrate explicitly or start a new comparable campaign segment and record the confounder.

## Enforce Budgets

Before every meta-intervention, estimate the minimum implementation, validation, and cleanup budget. Do not start if it would consume the terminal reserve or leave no meaningful observation window.

Stop normal iteration when any hard limit is reached. Also stop when:

- The user requests termination.
- No falsifiable VibeSys-owned hypothesis remains.
- Repeated infrastructure failures prevent useful evidence.
- Correctness or safety cannot be maintained.
- The remaining opportunity is smaller than the cost of another intervention.

## Handle Failures and Finish

Distinguish healthy silence, slow progress, capacity waits, stalls, crashes, and evidence-publication failures. Use phase-specific deadlines and progress timestamps rather than one universal timeout.

Before killing a suspected stall, capture a bounded process/status snapshot and recent log tail. Terminate both wrappers and owned child/remote resources. Preserve enough state to resume without rebuilding the entire context.

At run completion:

1. Run the terminal official evaluation when enabled.
2. Record final frontier and effectiveness metrics.
3. Retain, revise, or revert each intervention in the PR ledger.
4. Clean up processes, leases, and temporary resources.
5. Verify the worktree and campaign evidence are durable.
6. Mark the draft ready only if the retained changes are reviewable; otherwise close it with the final disposition.
