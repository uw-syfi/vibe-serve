# vs-project-state

Typed persistence for the `.vs` directory in an in-place VibeSys project.

The library owns portable project manifests, run manifests, completed-round
records, input fingerprints, and machine-local operational paths. It does not
invoke Git, construct agents, resolve compute backends, or read provider
credentials. Those application concerns remain in VibeSys.

## State layout

`ProjectStore` separates portable state from machine-local state:

```text
.vs/
├── .gitignore
├── project.json
├── runs/<run-id>/
│   ├── run.json
│   └── agent/rounds/NNNN.json
└── local/
    ├── current-run
    └── runs/<run-id>/
        ├── agent/active.json
        ├── logs/
        ├── round-transaction.json
        └── worktrees/
```

The `.vs/.gitignore` contract excludes `local/`. Callers decide whether to
commit the remaining portable metadata. This library never invokes Git.

Loop and subsystem code acquire a typed `StateNamespace` through
`portable_namespace(run_id, namespace)` or
`local_namespace(run_id, namespace)`. A namespace strictly loads and
atomically saves Pydantic models by safe relative path. `load_optional()`
returns `None` only for an absent file; malformed or schema-invalid state is an
error. Required loads report absence as `StateModelNotFoundError`.
`transition()` prepares an immutable `StateTransition` containing the exact
typed `StateDocument` replacement, or a deletion, and `apply()` commits that
transition atomically. This lets application transactions journal one validated
state change without depending on the model's owning loop package. A typed
`StateSlot` binds a namespace path to its Pydantic schema, so reconstructed
journal transitions are schema-validated before recovery applies them. Portable
namespaces also produce deterministic immutable
`StateSnapshot` values for application-level Git integration. Machine-local
namespaces cannot be snapshotted. The directory-returning methods remain only
for explicit path-based integrations such as an external search library.

`ProjectStore` also produces typed metadata snapshots without exposing path to
bytes mappings:

- `initialization_snapshot(run_id)` contains `.vs/.gitignore`, `project.json`,
  and that run's `run.json`.
- `run_manifest_snapshot(run_id)` contains one current `run.json`.
- `completed_round_snapshot(run_id, round_number)` contains one validated
  completed-round record.

These snapshots are portable selections for the application Git layer. Their
roots and relative files are validated together, and no snapshot can address
`.vs/local`.

## Run configuration

`RunConfiguration` is a strict discriminated union selected by `outer_loop`:

- `AgentRunConfiguration` for `outer_loop="agent"`
- `PlainRunConfiguration` for `outer_loop="plain"`
- `EvolveRunConfiguration` for `outer_loop="evolve"`

Each variant is immutable, rejects unknown fields, and records only sanitized
configuration needed to reproduce its loop. Construct a concrete variant in
application code. Pydantic discriminates the union when it loads a
`RunManifest`.
