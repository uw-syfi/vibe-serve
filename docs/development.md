# Development guide

This page is the starting point for contributors extending VibeSys. It links
to the focused guides for framework code, domains, skills, profilers, input
bundles, and the TUI.

## Before you change code

- Read [`docs/coding-best-practices.md`](coding-best-practices.md).
- Keep changes within the owning package and preserve the framework boundaries.
- Use the repository [pull request template](../.github/pull_request_template.md)
  when opening a PR.

## Repository layout

```text
src/vibesys/             Python framework and loop implementation
clients/tui/             TypeScript terminal client and launcher
libs/                    Reusable standalone libraries
examples/                Input bundles, evaluators, and candidate contracts
resources/skills/        Bundled Agent Skills and reference material
resources/profilers/     Profiler MCP servers and support packages
docs/                    Contributor and subsystem guides
tests/                   Python and integration tests
```

The main framework boundaries are:

- `src/vibesys/loops/` owns the outer-loop policies and shared loop helpers.
- `src/vibesys/agents/` owns the agent-runner abstraction and integrations.
- `src/vibesys/domains/` owns domain-specific prompt context and hooks.
- `src/vibesys/backends/` owns compute and execution backends.
- `examples/` owns target-specific objectives, manifests, evaluators, and
  candidate contracts.

## Local development

Run the Python checks from the repository root:

```bash
./scripts/check_format.sh
./scripts/check_lint.sh
uv run pytest
```

For a focused test, use for example:

```bash
uv run pytest tests/loops/plain/test_plain_loop.py
uv run pytest -k orchestrator
```

The TypeScript client has its own workflow; see
[`clients/tui/README.md`](../clients/tui/README.md). The short version is:

```bash
pnpm install --frozen-lockfile
pnpm --dir clients/tui generate:protocol
pnpm --dir clients/tui check
pnpm --dir clients/tui test
pnpm --dir clients/tui build
pnpm check:ts
```

When Python protocol models change, regenerate the files under
`clients/tui/src/generated/` and review the diff.

## Extend VibeSys

Use the guide that matches the surface you are adding:

- [Add or customize a domain](../src/vibesys/domains/README.md) for new
  problem-space prompts, hooks, and domain registration.
- [Add or update Agent Skills](../resources/skills/README.md) and read the
  [VibeSys skill metadata guide](skill-metadata.md) when routing skills by
  backend or domain.
- [Extend profilers](extending-profilers.md) for profiler support packages,
  MCP tools, and profiler prompts.
- [Create a model-serving input bundle](../.agents/skills/vs-init/SKILL.md)
  for a new model, hardware target, or workload.
- [Update CLI flags and combinations](cli-flags.md) when changing the user
  facing command contract.
- [Update feature flags](../src/vibesys/FEATURE_FLAGS.md) for opt-in
  experiments and optional framework behavior.

The experimental Omnigent adapter is a developer-facing alternative to the
standard CLI adapter. It currently supports Claude and Codex on the host path
only; see the feature-flag guide before enabling it.

Keep target-specific APIs, ABIs, ownership rules, and service protocols in the
input bundle's `CANDIDATE_CONTRACT.md` or design documentation rather than in
the neutral framework prompts.

## Internal tools and workflows

The plain loop's issue MCP server is an internal development tool:

```bash
uv run vibesys-issue-mcp
```

For issue forms and repository issue conventions, see
[`docs/issue-authoring.md`](issue-authoring.md). For evolutionary search policy
work, see [`docs/openevolve.md`](openevolve.md).
