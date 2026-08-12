# vs-loop-state

Typed persisted-state models, stable JSON-compatible codecs, and in-memory
agent-loop history behavior.

The public API exported from `vs_loop_state` has three groups:

- `RoundRecord` defines one validated completed-round record.
- `serialize_round_record` and `parse_round_record` define its portable JSON
  representation.
- `RoundHistory` collects records in memory and resolves rollback bases.
- `PlainLoopCursor`, `PlainPerformanceRecord`, and
  `PlainPerformanceSnapshot` define versioned plain-loop state.
- `IndividualRecord` and `PopulationSnapshot` define a versioned evolve archive
  with validated IDs, lineage references, and finite fitness metrics.

Plain and evolve persisted models reject unknown fields and type coercion.
Performance timestamps require timezone information. The `serialize_*`
functions return JSON-compatible dictionaries, and matching `parse_*`
functions validate those dictionaries without reading files.

The library does not read or write files. `vs-project-state` owns `.vs` project
layout and persistence. VibeSys owns Git operations, orchestration, and the
application-specific vocabularies stored in string fields.
