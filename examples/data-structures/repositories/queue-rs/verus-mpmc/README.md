# Verus MPMC FIFO Candidate

This isolated subcrate explores a verifier-gated queue candidate without
changing the existing C ABI queue fixture. It exposes a bounded pure-Rust
`MpmcFifo<T>` through fixed `new`, `enqueue`, `dequeue`, and `len` operations.

The task owns every file outside `src/candidate/**`, including the manifest,
lockfile, module wiring, contract, facade, README, and ignore rules.
`FifoToken<T>` is the client-owned view of the abstract `Seq<T>` history. The
fixed facade gives each operation an `AtomicUpdate` whose postcondition defines
exact bounded FIFO behavior, then delegates that obligation to the candidate.

This is a safety and strict-FIFO prototype. It does not prove lock acquisition
termination, starvation freedom, or weak-memory properties beyond those
supplied by Verus's sequentially consistent atomic library.

The candidate owns its representation, synchronization, invariants, operation
bodies, and the step that resolves each logical update. It may also transfer an
update through a candidate invariant for helping. The fixed facade contains no
runtime synchronization and does not choose a physical linearization point.

The Verus standard-library dependency is pinned to the release matching
`Verus 0.2026.08.30.b432e82`.

```bash
cargo check --manifest-path verus-mpmc/Cargo.toml
cargo test --manifest-path verus-mpmc/Cargo.toml
cargo verus verify --manifest-path verus-mpmc/Cargo.toml
```
