# Omnigent agent backend — live probe

`live_turn.py` is the end-to-end proof for the opt-in `omnigent_agent_backend`
feature flag. It drives a real turn through
`vibesys.agents.omnigent.runner.OmnigentAgentRunner` against a locally
authenticated coding-agent CLI, in a throwaway workspace.

It lives here rather than under `tests/` because it needs credentials, network
access, a real CLI binary, and a working sandbox backend — none of which CI has.
The parts that *can* be tested hermetically are in
`tests/agents/test_omnigent_backend.py`.

## Running it

Requires `bwrap` on Linux (or `sandbox-exec` on macOS) and an authenticated
`claude` or `codex` CLI. The omnigent dependency comes with the dev group:

```bash
uv sync --dev
```

```bash
uv run python experiments/omnigent-agent-backend/live_turn.py claude
```

```bash
uv run python experiments/omnigent-agent-backend/live_turn.py codex
```

## What it checks

| Check | Why it matters |
| --- | --- |
| Reads `NOTES.md` from the workspace | Proves the agent has working `sys_os_*` tools and is rooted in the workspace, not just confined to it |
| Structured Pydantic response parses | Proves the schema hint and `parse_typed_response_text` path survive the Omnigent turn |
| Two `usage.jsonl` records written | Proves the audit trail matches the agentshim backend's schema |

Both providers pass all three. The findings this probe produced — and the three
defects it caught that unit tests could not — are recorded in
[`docs/omnigent-evaluation.md`](../../docs/omnigent-evaluation.md).

## Known noise

`claude_agent_sdk` emits `RuntimeError: Event loop is closed` from subprocess
transport `__del__` during interpreter shutdown. It is reproducible with the raw
SDK, independent of VibeSys, and appears after all results are computed. The
runner keeps one long-lived event loop and exposes `close()` precisely so cached
executors are not stranded on a dead loop mid-run.
