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
| `omnigent_agent_backend` | `false` | Compatibility alias for `[agent].driver = "omnigent"`. |

### `omnigent_agent_backend`

New configuration should select the optional runtime directly:

```toml
[agent]
backend = "cli"
driver = "omnigent"
```

Omitting `driver` selects `agentshim`. The feature flag remains as a
compatibility alias while existing experiments migrate. Setting the flag and
explicitly selecting `agentshim` is rejected as conflicting configuration.

Enabling it requires the optional extra. Contributors already have it —
`uv sync --dev` pulls `vibesys[omnigent]` — but an end-user install needs:

```bash
uv sync --extra omnigent
```

```toml
[feature_flags]
omnigent_agent_backend = true
```

The `AgentClient` presents one application interface over both drivers. It owns
session reuse, skill setup, response parsing, logging, usage records, and
lifecycle. A driver owns native executor setup, policy translation, turns,
events, and cleanup. Unsupported requirements fail before a session starts.

Current Omnigent constraints:

- Only the `claude` and `codex` providers are supported. Omnigent 0.6.0 ships
  no Gemini harness, and its `opencode-native` executor is a bridge for
  Omnigent's own web UI (it takes no `cwd`/`model`), so neither can run a
  headless VibeSys turn.
- `--docker` is rejected; this integration has no container-launcher support.
- MCP server setup is rejected; the current Omnigent driver does not translate
  VibeSys MCP specifications.
- Extra host resource grants are rejected. The agentshim path declares these
  through `vs_sandbox`; the Omnigent path imports only the installed Rust
  toolchain automatically, so it cannot honour arbitrary grants.
- Top-level dot paths in the project policy are supported. Hidden dot paths
  remain masked, while control directories such as `.git` and `.vibesys` are
  exposed. Nested paths and non-dot paths are rejected.

Sandboxing differs between the two backends. The agentshim path wraps the agent
in a `vs_sandbox` host sandbox; the Omnigent path expresses the same intent in
Omnigent's vocabulary: an `OSEnvSpec` whose sandbox grants write access to the
workspace and read access to the Rust toolchain, with the backend chosen per
platform (bubblewrap on Linux, Seatbelt on macOS) and never set to `none`.
VibeSys resolves the workspace's active Rust sysroot with auto-install disabled,
exposes only its `bin`, `lib`, and optional `libexec` trees, and gives each
executor an ephemeral writable Cargo home. The scratch directory is removed
when the executor closes. Cargo keeps its conventional workspace `target`
directory; VibeSys permits Cargo's generated `.cargo-lock`, `.fingerprint`, and
`.rustc_info.json` basenames through Omnigent's recursive hidden-path mask so
later shell helpers can reuse the build. This bypasses rustup's host metadata
and avoids recursively exposing or scanning `~/.rustup`.
Omnigent 0.6.0 cannot make `.git` and `.vibesys` read-only beneath a writable
workspace. The run contract therefore protects those control directories, and
local operational state lives outside the repository by default. The two
mechanisms have not been proven equivalent.

Confining the agent is not the same as equipping it. Omnigent routes file and
shell access through `sys_os_read` / `sys_os_write` / `sys_os_edit` /
`sys_os_shell` MCP tools that the caller must build and dispatch, so the runner
does that too — without it the agent starts sandboxed and toolless. Reaching
that seam requires assigning Omnigent's private `_tool_executor` attribute,
which is the most upgrade-fragile line in the integration. The Codex executor's
native filesystem tools are disabled so all file and shell operations use this
sandboxed path.

Requires the platform sandbox backend — `bwrap` on Linux
(`apt install bubblewrap`) or `sandbox-exec` on macOS. Omnigent resolves it when
the agent's OS environment is created; if it is missing, the driver raises
`OmnigentDriverError` naming the remedy rather than running the agent
unconfined. GitHub's runners do not ship `bwrap`, so the tests that build a real
OS environment skip there under the repo's existing
`VIBESYS_REQUIRE_SANDBOX_TESTS` convention.

The automated tests cover provider wiring, sandbox construction, tool dispatch,
event handling, and teardown. Credentialed live CLI validation is intentionally
outside the repository's test suite.

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
