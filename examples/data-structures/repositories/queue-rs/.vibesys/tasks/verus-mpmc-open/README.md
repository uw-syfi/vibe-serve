# Open Verus MPMC FIFO Task

This task explores proof-carrying generation of a concurrent data structure in
Rust and Verus. It uses the candidate library at `verus-mpmc/` directly, with no
C ABI or shared-library adapter.

The accuracy gate performs three checks:

1. `cargo check --locked` compiles the candidate as ordinary Rust.
2. `cargo verus verify --locked` verifies every opted-in candidate module.
3. A task-owned Rust harness checks the fixed public interface, sequential
   boundary behavior, FIFO order across non-overlapping producer operations,
   and concurrent-consumer conservation.

Before verification, the runner rejects disabled Verus metadata and common
proof bypasses such as `assume`, `admit`, axioms, and external bodies. This is a
fail-closed prototype policy, not a complete adversarial source validator.

The VibeSys manifest builds and uses the pinned development image in
`container/Dockerfile`. It pins the Ubuntu base image by digest, Verus
`0.2026.08.30.b432e82` and its release archive checksum, rustup `1.28.2` and its
checksum, and Rust `1.97.1`. Run the same container workflow from the repository
root:

```bash
python3 .vibesys/tasks/verus-mpmc-open/container.py check
python3 .vibesys/tasks/verus-mpmc-open/container.py benchmark \
  --duration-seconds 1 --output-json results.json
```

Docker with `linux/amd64` support is the only host prerequisite. For local
iteration without the container, put the same `cargo-verus`, `verus`, Rust, and
`vstd` release on `PATH` and invoke `runner.py` directly. The runner stages the
immutable Rust harness under `target/` before compiling it, so Cargo never
writes into `.vibesys/`.

The Verus source, ghost model, invariants, and proof lemmas are intentionally
editable. This is an open proof track, not a certified-template track. A Verus
success proves only the properties actually stated by the candidate, subject to
its admitted assumptions and Verus's trusted computing base. The fixed task
contract and independent harness make spec weakening visible, but do not turn
the candidate-owned specification into a trusted formal specification.

This first draft therefore establishes the toolchain and executable proof
shape, but is not yet the final correctness gate. The next step is a task-owned
Verus interface that requires a compositional logically atomic contract from
the candidate without fixing its representation, linearization points, or
ghost-state organization.
