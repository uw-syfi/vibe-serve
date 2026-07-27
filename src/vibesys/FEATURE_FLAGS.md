# VibeSys Feature Flags

VibeSys declares feature flags in `src/vibesys/features.py`.

The generic utilities come from `vs_feature_flags`, but the VibeSys-specific
manifest, defaults, config parsing, and call-site conventions live here.

## Manifest

Add flags to the `FeatureFlag` enum and define each one in `FEATURES`:

```python
from enum import StrEnum

from vs_feature_flags import FeatureDefinition, FeatureRegistry


class FeatureFlag(StrEnum):
    EXAMPLE_FEATURE = "example_feature"


FEATURES = FeatureRegistry(
    FeatureFlag,
    {
        FeatureFlag.EXAMPLE_FEATURE: FeatureDefinition(
            description="Enable the example feature.",
            default=False,
        ),
    },
)
```

`example_feature` is a non-product sample flag used to exercise feature flag
plumbing and tests. Remove it when the first real VibeSys feature flag exists.

## Current Flags

| Flag | Default | Effect |
| --- | --- | --- |
| `example_feature` | `false` | Sample flag; exercises the plumbing only. |
| `omnigent_agent_backend` | `false` | Runs the `cli` agent backend through Omnigent's in-process executor instead of agentshim. |

### `omnigent_agent_backend`

Opt-in and unproven — see [`docs/omnigent-evaluation.md`](../../docs/omnigent-evaluation.md)
for why the evaluation landed on "retain agentshim as the default". With the
flag off, nothing under `vibesys/agents/omnigent/` is imported and the
agentshim path is unchanged.

Enabling it requires the optional extra. Contributors already have it —
`uv sync --dev` pulls `vibesys[omnigent]` — but an end-user install needs:

```bash
uv sync --extra omnigent
```

```toml
[feature_flags]
omnigent_agent_backend = true
```

Constraints, each of which raises `OmnigentUnavailableError` naming the remedy
rather than silently falling back to agentshim:

- Only the `claude` and `codex` providers are supported. Omnigent 0.6.0 ships
  no Gemini harness, and its `opencode-native` executor is a bridge for
  Omnigent's own web UI (it takes no `cwd`/`model`), so neither can run a
  headless VibeSys turn.
- `--docker` is rejected. The container launcher is still a prototype under
  `experiments/omnigent-docker-spike/`.
- Per-invocation MCP server injection is rejected; Omnigent wires MCP through
  its own agent spec, which this integration does not construct.
- Extra host resource grants are rejected. The agentshim path declares these
  through `vs_sandbox`; this integration confines the agent to its workspace
  and nothing else, so it cannot honour them.

Sandboxing differs between the two backends. The agentshim path wraps the agent
in a `vs_sandbox` host sandbox; the Omnigent path expresses the same intent in
Omnigent's vocabulary — an `OSEnvSpec` whose sandbox grants write access to the
workspace only, with the backend chosen per platform (bubblewrap on Linux,
Seatbelt on macOS) and never set to `none`. The two mechanisms have **not** been
proven equivalent, which is one more reason the flag is off by default.

Confining the agent is not the same as equipping it. Omnigent routes file and
shell access through `sys_os_read` / `sys_os_write` / `sys_os_edit` /
`sys_os_shell` MCP tools that the caller must build and dispatch, so the runner
does that too — without it the agent starts sandboxed and toolless. Reaching
that seam requires assigning Omnigent's private `_tool_executor` attribute,
which is the most upgrade-fragile line in the integration.

Requires `bwrap` on Linux (or `sandbox-exec` on macOS). A live end-to-end probe
lives at `experiments/omnigent-agent-backend/live_turn.py`; both supported
providers pass it.

## Config

`src/vibesys/config.py` parses `[feature_flags]` from `agent.toml` with
`parse_feature_flag_overrides` and stores typed overrides in
`config.feature_flags`.

Example:

```toml
[feature_flags]
example_feature = true
```

Unknown flag names and non-boolean values fail during config loading.

## Usage

Use enum members at call sites:

```python
from vibesys.features import FeatureFlag, is_feature_enabled


if is_feature_enabled(FeatureFlag.EXAMPLE_FEATURE, config):
    ...
```

For direct registry access:

```python
from vibesys.features import FEATURES, FeatureFlag


enabled = FEATURES.is_enabled(
    FeatureFlag.EXAMPLE_FEATURE,
    config.feature_flags,
)
```

## Adding A Flag

1. Add the enum member to `FeatureFlag`.
2. Add a matching `FeatureDefinition` to `FEATURES`.
3. Add `[feature_flags]` config examples only if users are expected to set it.
4. Use `FeatureFlag.YOUR_FLAG`, not raw strings, at call sites.
5. Test the default behavior and the overridden behavior.

## Removing A Flag

1. Delete the enum member and its `FeatureDefinition`.
2. Run tests to catch stale `FeatureFlag.YOUR_FLAG` references.
3. Remove any corresponding `agent.toml` examples or docs.
