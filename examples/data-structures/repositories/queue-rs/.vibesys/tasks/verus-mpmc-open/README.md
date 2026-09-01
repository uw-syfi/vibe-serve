# Open Verus MPMC FIFO Task

This task explores proof-carrying generation of a concurrent data structure in
Rust and Verus. It uses the candidate library at `verus-mpmc/` directly, with no
C ABI or shared-library adapter.

The accuracy gate performs three checks:

1. `cargo check --locked` compiles the candidate as ordinary Rust.
2. `cargo verus verify --locked` verifies every opted-in candidate module.
3. The task-owned `accuracy/` crate checks the fixed public interface, sequential
   boundary behavior, FIFO order across non-overlapping producer operations,
   and concurrent-consumer conservation.

The separate task-owned `benchmark/` crate contains only the native producer and
consumer workload and reports `total_ops_per_sec`. Neither crate is part of the
verified candidate, and neither uses the native queue task's C ABI.

Before verification, the runner rejects changes to `Cargo.toml`, `src/lib.rs`,
`src/contract.rs`, or `src/api.rs`, as well as common proof bypasses such as
`assume`, `admit`, axioms, and external bodies. Implementations may change only
files below `src/candidate/`. This is a fail-closed prototype policy, not a
complete adversarial source validator.

The task-local `Dockerfile` includes Git, build tools, Python, and the
verification toolchain. It pins the Ubuntu base image by digest, Verus
`0.2026.08.30.b432e82` and its release archive checksum, rustup `1.28.2` and its
checksum, and Rust `1.97.1`. The Verus release is x86-only, so the Dockerfile
selects `linux/amd64` explicitly.

Run the task from the `queue-rs` repository root:

```bash
vibesys --outer-loop agent \
  --task verus-mpmc-open \
  --runs-dir /absolute/path/to/vibesys-runs --local \
  --backend cpu --profiler none \
  --max-rounds 4
```

From the VibeSys source checkout root, the equivalent command is:

```bash
uv run vibesys \
  --outer-loop agent \
  --project examples/data-structures/repositories/queue-rs \
  --task verus-mpmc-open \
  --runs-dir /absolute/path/to/vibesys-runs --local \
  --backend cpu --profiler none \
  --max-rounds 4
```

Docker with `linux/amd64` support is the only host prerequisite for that
workflow. VibeSys detects the conventional task Dockerfile, builds it with the
task directory as its context, and automatically runs both agents and gates in
the resulting image. Docker layer caching makes subsequent launches
incremental. No separate `docker build`, `--docker`, or `--docker-image` step is
required.

For gate iteration inside an equivalent environment, invoke `runner.py`
directly. The runner stages the immutable accuracy or benchmark crate under
`target/` before compiling it, so Cargo never writes into `.vibesys/`.

```bash
python3 .vibesys/tasks/verus-mpmc-open/runner.py check
python3 .vibesys/tasks/verus-mpmc-open/runner.py benchmark \
  --duration-seconds 1 --output-json results.json
```

The fixed `FifoStorage` contract owns the abstract `Seq<T>` transitions. The
fixed facade owns synchronization and calls the candidate implementation only
through that contract. This makes the seed proof non-vacuous, but constrains
every operation to linearize while holding the facade's lock. The next step for
the open track is a compositional logically atomic interface that preserves the
same fixed transitions while allowing candidate-owned synchronization and
linearization points.
