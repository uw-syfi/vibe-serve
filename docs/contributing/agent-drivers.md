# Agent Drivers

`AgentClient` presents one application interface over the supported drivers. It
owns session reuse, skill setup, response parsing, logging, usage records, and
lifecycle. A driver owns native executor setup, policy translation, turns,
events, and cleanup. Unsupported requirements fail before a session starts.

Omitting `driver` selects `agentshim`. Select the optional Omnigent driver
directly:

```toml
[agent]
backend = "cli"
driver = "omnigent"
```

Contributors get the dependency from `uv sync --dev`. End-user installations
need the optional extra:

```bash
uv sync --extra omnigent
```

## Omnigent constraints

- Only the `claude` and `codex` providers are supported. Omnigent 0.10.0 has no
  Gemini harness, and its `opencode-native` executor cannot run a headless
  VibeSys turn.
- `--docker` is rejected because the integration has no container launcher.
- MCP server setup is rejected because the driver does not translate VibeSys
  MCP specifications.
- Extra host resource grants are rejected. The Omnigent path imports only the
  installed Rust toolchain automatically.
- Hidden project paths become explicit Omnigent masks. Read-only declarations
  are accepted only for top-level dot paths such as `.git` and `.vibesys`.
  Those paths are protected by the agent contract, not sandbox enforcement.

## Sandboxing

The agentshim driver wraps the agent in a `vs_sandbox` host sandbox. The
Omnigent driver builds an `OSEnvSpec` that grants workspace write access and
narrow read access to the active Rust toolchain. It selects bubblewrap on Linux
or Seatbelt on macOS and never permits an unconfined fallback.

VibeSys exposes only the Rust sysroot's `bin`, `lib`, and optional `libexec`
trees. Each executor gets an ephemeral writable Cargo home, removed when the
executor closes. Cargo keeps its conventional workspace `target` directory.
Declared hidden paths and `.codex-tmp` are explicitly masked. Top-level dot-path
scanning fails if it exceeds Omnigent's limit instead of silently exposing
paths.

Omnigent 0.10.0 cannot make `.git` and `.vibesys` read-only beneath a writable
workspace. Local operational state therefore lives outside the repository by
default, and the run contract protects those directories. This has not been
proven equivalent to sandbox enforcement.

Omnigent routes file and shell access through its `sys_os_*` tools. The driver
builds and dispatches those tools, currently through Omnigent's private
`_tool_executor` attribute. Codex native filesystem tools are disabled so all
file and shell operations use this sandboxed path.

The host must provide `bwrap` on Linux or `sandbox-exec` on macOS. If it is
missing, the driver raises `OmnigentDriverError` instead of running unconfined.
GitHub's Linux runners do not provide `bwrap`, so real OS-environment tests skip
there unless `VIBESYS_REQUIRE_SANDBOX_TESTS` is enabled.

Automated tests cover provider wiring, sandbox construction, tool dispatch,
event handling, and teardown. Credentialed live CLI validation is outside the
repository test suite.
