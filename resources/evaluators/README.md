# Evaluator packages

This directory contains local development builds of reusable VibeSys evaluator
packages. Task definitions depend on package names and exact versions, not on
these source paths. The runtime resolves a package to immutable contents and
records its `sha256:` digest with the run.

Each immediate child is a self-contained package with a
`vibesys.evaluator.toml` file:

```toml
schema_version = 1
name = "vibesys-evaluator-example"
version = "0.1.0"
protocol_version = 1

[entrypoints]
example-check = ["example-check"]
```

Entry points are logical public names mapped to argv prefixes. Local source
packages may use the literal `${PACKAGE_ROOT}` token in an argv element. The
resolver expands it to the absolute package directory, allowing commands to run
from a candidate repository. Arguments declared by a task are appended to the
resolved prefix without invoking a shell. A task argument may use
`${PROJECT_ROOT}` when the evaluator needs an absolute path to the candidate.
The selected run environment expands it to the candidate repository root before
running the resolved command. This is necessary for tools such as `go -C`,
which change cwd.

The local collection is the initial package source. Published packages can
later provide the same metadata and entry-point names through a registry-backed
resolver. Repository-specific checks and workloads belong with their task, not
in this directory.
