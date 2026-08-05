# Evidence and Infrastructure

## Build a Chain of Trust

Bind every trusted result to:

- Exact candidate source and build inputs, not just the current worktree or a commit label.
- Objective and evaluator versions.
- Workload parameters, model/data identifiers, seeds, and baseline flags.
- Hardware identity and relevant runtime configuration.
- Raw request results, logs, and aggregated metrics.
- Accuracy outcome and any failed gates.
- Profiling activation evidence when a profiler-informed claim is made.

Write immutable artifacts before restoring or mutating the candidate. A hash without retained bytes is insufficient if those bytes cannot later be reconstructed.

Pass artifact paths between agents. Do not interpolate free-form implementer evidence into judge instructions. Parse structured fields defensively, and treat all agent-authored text as untrusted data.

## Keep Official and Provisional Evidence Distinct

Focused agent-run measurements are provisional and useful for iteration. Only framework-run measurements that satisfy the complete experiment contract should update the trusted frontier or official plateau history.

An official record should contain the full objective metric vector, not only the scalar used for headline ranking. Ensure resumed campaigns preserve compatible legacy history or perform an explicit migration; do not silently reinterpret missing fields as failure.

When benchmark infrastructure fails, classify the failure separately from candidate performance. Do not call a candidate slow because deployment did not start, the health probe timed out, or measurement never began.

## Verify the Real Path Activated

For every optimization, identify an observable that proves traffic executed the intended path:

- Nonzero scoped counter tied to completed requests.
- Trace or profile frames in the optimized function.
- Log marker with request/candidate identity.
- Deliberate A/B perturbation that changes the expected outcome.
- Binary/build metadata proving the deployed artifact contains the change.

An imported package, environment variable, configuration file, or successful compilation proves availability, not activation. A zero counter is normally missing or negative evidence, not success.

Profile the production request path at representative load. A profiler attached to a control process, idle worker, localhost-only shortcut, or different deployment cannot attribute the official gap.

Treat profiler advice as non-blocking. If profiling is unavailable, state uncertainty and use alternative discriminating evidence rather than inventing a bottleneck.

## Guard Against Reward Hacking

Reject candidates that improve the measured score without performing the target computation, including:

- Replaying known outputs or completed trajectories to later requests.
- Special-casing benchmark prompts or accuracy answers.
- Reporting fabricated usage or splitting/combining stream frames to manipulate token counting.
- Skipping required model work while imitating the public response shape.
- Measuring a private fast path not exercised by the canonical client.

Legitimate live batching, prefix sharing, caching of reusable model state, and cohort sharing are allowed when they preserve request semantics and apply generally. Define this boundary in the experiment contract and test randomized or withheld cases.

Prefer trusted server-side token usage when independently verifiable; otherwise tokenize emitted output consistently in the evaluator. Do not equate SSE frame count with token count.

## Put Deterministic Mechanics in the Framework

The framework should:

- Materialize the candidate into a known target directory.
- Save candidate-bound provenance before paid work.
- Restore checkpoints after read-only roles and rejected candidates.
- Exclude persistent caches and environments from source restoration.
- Launch, probe, monitor, timeout, terminate, and clean up processes.
- Run accuracy and benchmark commands with captured stdout, stderr, status, and timestamps.
- Enforce cumulative budgets across retries and continuations.

Do not ask agents to repeatedly run Git restoration recipes, reconstruct virtual environments, package validation bundles, or decide whether an accelerator lease should be cleaned up. Agents may diagnose framework failures and propose repairs; routine execution remains deterministic.

Using `git checkout <hash>` alone is not a complete restoration design. It may leave untracked files, omit ignored runtime state intentionally, and conflate source identity with deployed bytes. Restore only the framework-owned candidate tree from a validated snapshot while preserving explicitly declared caches outside that tree.

## Reuse Expensive State Safely

Preserve:

- Package/download caches.
- Virtual environments when their dependency fingerprint matches.
- Compiled artifacts keyed by inputs and toolchain.
- Model weights and immutable data.
- Healthy deployment/container instances within a lease.

For parameter sweeps, start the target once and vary workload points against the same healthy instance unless a parameter changes startup state. Record warmup separately and avoid charging cold start to steady-state serving metrics unless the objective includes it.

Every reusable remote resource needs:

- An owner/lease identifier.
- Maximum lifetime and idle timeout.
- Health and candidate-identity checks before reuse.
- Cleanup on success, error, interruption, and orchestrator exit.
- An external or provider-level expiry as a final safety net.

Do not use an unrealistically short health timeout to decide that a scaled-to-zero deployment is unreusable; distinguish cold wakeup from invalid identity.

## Diagnose Hangs and Slow Rounds

Emit phase-level status and timestamps for:

1. Agent inference.
2. Local preparation/build.
3. Deployment startup.
4. Health/warmup.
5. Accuracy.
6. Focused or official benchmark.
7. Judge review.
8. Snapshot/restore/cleanup.

Monitor both the host-side client and the in-environment child process. Killing only a wrapper can leak an agent, server, or accelerator-backed container. On timeout, capture bounded diagnostic state before termination: process tree, recent logs, resource status, and active phase.

Use backoff for long expected remote phases, but keep user-visible progress below the interaction timeout. Distinguish:

- Slow but making progress.
- Stalled with no observable progress.
- Waiting for capacity or external service.
- Crashed with a leaked child.
- Completed but failed to publish evidence.

## Keep Core Policy Environment-Neutral

Express orchestration in capabilities such as `build`, `launch`, `health`, `profile`, `evaluate`, `terminate`, and `retain`. Implement provider-specific behavior behind environment adapters and runtime manuals.

Avoid embedding provider lifecycle rules, language-specific environment commands, server-framework assumptions, or a current entry point in general prompts. The campaign should remain able to adopt another implementation substrate, server framework, execution provider, or local target without rewriting role semantics.
