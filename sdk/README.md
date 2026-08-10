# SDK

Reusable packages for authors of VibeSys input bundles.

Unlike `libs/`, which houses framework-internal packages imported by VibeSys
core, packages here are consumed by user-authored code that runs inside
VibeSys-managed environments (benchmarks, accuracy checkers, reference
generators). The VibeSys framework itself does not import these packages; it
only observes their output (JSON results, exit codes).

Each package is independently installable with minimal dependencies so that
benchmark and checker scripts can run in lean, purpose-built environments
without pulling in the full VibeSys dependency tree.

## Packages

| Package | Purpose |
|---------|---------|
| `vs-bench` | Benchmark toolkit: SSE transport, load scheduling, statistics |
