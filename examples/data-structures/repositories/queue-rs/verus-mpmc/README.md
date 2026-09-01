# Verus MPMC FIFO Candidate

This isolated subcrate explores a verifier-gated queue candidate without
changing the existing C ABI queue fixture. It exposes a bounded pure-Rust
`MpmcFifo<T>` and uses Verus's verified reader-writer lock for synchronization.

The sequential core has an immutable `Seq<T>` view. Verification checks that a
successful enqueue appends to that sequence and that a successful dequeue
returns and removes element zero. The lock invariant keeps the sequence length
within the construction-time capacity. This is a safety and strict-FIFO
prototype. It does not prove lock acquisition termination, starvation freedom,
or weak-memory properties beyond those supplied by Verus's sequentially
consistent atomic library.

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
