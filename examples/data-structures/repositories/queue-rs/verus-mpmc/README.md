# Verus MPMC FIFO Candidate

This isolated subcrate explores a verifier-gated queue candidate without
changing the existing C ABI queue fixture. It exposes a bounded pure-Rust
`MpmcFifo<T>` and uses Verus's verified reader-writer lock for synchronization.

The task owns `Cargo.toml`, `src/lib.rs`, `src/contract.rs`, and `src/api.rs`.
Implementers may change only `src/candidate/**`. The fixed contract gives the
candidate storage an immutable `Seq<T>` view and requires enqueue to append and
dequeue to return and remove element zero. The fixed facade's lock invariant
keeps the sequence length within the construction-time capacity.

This is a safety and strict-FIFO prototype. It does not prove lock acquisition
termination, starvation freedom, or weak-memory properties beyond those
supplied by Verus's sequentially consistent atomic library.

The public methods do not yet expose Verus `AtomicUpdate` or another logically
atomic client contract. The current proof establishes sequential refinement
inside the lock and relies on the lock's verified serialization for concurrent
composition.

The Verus standard-library dependency is pinned to the release matching
`Verus 0.2026.08.30.b432e82`.

```bash
cargo check --manifest-path verus-mpmc/Cargo.toml
cargo test --manifest-path verus-mpmc/Cargo.toml
cargo verus verify --manifest-path verus-mpmc/Cargo.toml
```
