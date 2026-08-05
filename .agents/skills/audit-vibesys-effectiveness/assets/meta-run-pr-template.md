## Problem

### Audit scope

- Meta-run:
- VibeSys parent commit:
- Campaign and round window:
- Objective and environment:
- Prompt, skill, evaluator, and policy versions:
- Evidence index paths:
- Missing instrumentation:

### Baseline effectiveness

| Category | Finding | Evidence | Baseline measure | Confidence |
| --- | --- | --- | --- | --- |
| Outcome effectiveness | | | | |
| Learning effectiveness | | | | |
| Operational efficiency | | | | |
| Coordination quality | | | | |
| Evidence integrity | | | | |
| Search quality and generality | | | | |
| Other | | | | |

### Limiting VibeSys mechanism

- Failure classification:
- Causal mechanism:
- Confounders and alternatives:

## Solution

### Meta-hypothesis

- Proposed VibeSys intervention:
- Why it improves discovery rather than leaking an answer:
- Expected meta-metric effects:
- Possible regressions:
- Success criteria:
- Reversion condition:
- Generality claim:

### Architecture

- Change surface and owner:
- Candidate/system boundary:
- Framework, prompt, skill, adapter, and artifact responsibilities:

### Intervention log

| Commit | Rationale | Scope | Validation status |
| --- | --- | --- | --- |
| | | | |

### Leakage review

- [ ] Prompts contain procedures and contracts, not candidate optimizations or benchmark answers.
- [ ] Reusable domain knowledge is isolated in versioned, selectively loaded skills.
- [ ] Skill-set changes are recorded as capability changes rather than planning-policy gains.
- [ ] Campaign-specific findings remain in artifacts referenced by path.

## Verification

### Effectiveness tracker

| Checkpoint | Campaign window | Meta-metrics | Comparison/control | Confounders | Interpretation |
| --- | --- | --- | --- | --- | --- |
| Baseline | | | | | |

### Correctness properties

- VibeSys system invariant:
- Evidence-integrity invariant:
- Generality or compatibility boundary:

### Testing

- Command or workflow:
- Result:

### Disposition

- Current status: `Proposed`, `Validating`, `Retain`, `Revise`, `Revert`, `Inconclusive`, or `No change`.
- Decision evidence:
- Follow-up:
