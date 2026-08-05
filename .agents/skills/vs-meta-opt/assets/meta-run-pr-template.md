## Problem

### Audit scope

- VS Meta Opt run:
- VibeSys parent commit:
- Specified input paths and revisions:
- Exact launch/resume command:
- Campaign and round window:
- Objective and environment:
- Prompt, skill, evaluator, and policy versions:
- Evidence index paths:
- Missing instrumentation:

### Run control

- Maximum rounds:
- Maximum wall-clock time:
- Maximum accelerator time or cost:
- Maximum agent turns or inference budget:
- Maximum meta-interventions:
- Terminal evaluation and cleanup reserve:
- Current remaining budgets:
- Last completed round and current phase:
- Last progress time and event/log cursor:
- Next wake reason and interval:

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
- Project conventions followed:
- Existing abstractions reused or extended:
- New abstraction or adjacent refactor and its justification:
- Special-case or compatibility paths intentionally avoided:

### Generated schema migration (if applicable)

- Generated code or state affected:
- Pre-migration snapshot:
- New canonical schema:
- One-way migration and validation:
- Confirmation that ordinary runtime paths no longer implement the old schema:

### Intervention log

| Commit | Rationale | Scope | Validation status |
| --- | --- | --- | --- |
| | | | |

### Campaign segments

| Segment | VibeSys commit | Checkpoint and rounds | Input and command | Active meta-hypothesis | End reason and evidence |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

### Counterfactual trajectory reviews

| Round/window | Trigger | Independent review artifacts | Review cost | VibeSys proposal | Comparison | Attributed failure | System response |
| --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | |

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
