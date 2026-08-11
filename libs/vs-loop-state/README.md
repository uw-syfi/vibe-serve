# vs-loop-state

Typed round records and stable JSON codecs for VibeSys loop state.

The library owns `RoundRecord`, `RoundHistory`, round serialization and parsing,
atomic history persistence, and rollback-base resolution. Its public API is
exported from `vs_loop_state`. It does not own Git operations, `.vs` project
layout, agent orchestration, or the application-specific vocabularies stored in
round fields. Those concerns remain in VibeSys and `vs-project-state`.
