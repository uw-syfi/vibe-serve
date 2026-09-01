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

Before verification, the runner rejects disabled Verus metadata and common
proof bypasses such as `assume`, `admit`, axioms, and external bodies. This is a
fail-closed prototype policy, not a complete adversarial source validator.

The pinned development image in `container/Dockerfile` includes Git, build
tools, Python, and the verification toolchain. It pins the Ubuntu base image by digest, Verus
`0.2026.08.30.b432e82` and its release archive checksum, rustup `1.28.2` and its
checksum, and Rust `1.97.1`.

Build it once from the `queue-rs` directory:

```bash
docker build --platform linux/amd64 \
  --file .vibesys/tasks/verus-mpmc-open/container/Dockerfile \
  --tag vibesys-verus-mpmc:0.2026.08.30-b432e82 \
  .vibesys/tasks/verus-mpmc-open/container
```

Resolve the immutable local image ID, then run the actual task with VibeSys
owning the container boundary:

```bash
verus_image_id=$(docker image inspect --format '{{.Id}}' \
  vibesys-verus-mpmc:0.2026.08.30-b432e82)

vibesys --outer-loop agent \
  --task verus-mpmc-open \
  --runs-dir /absolute/path/to/vibesys-runs --local \
  --backend cpu --profiler none \
  --docker --docker-image "$verus_image_id" \
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
  --docker --docker-image "$verus_image_id" \
  --max-rounds 4
```

Docker with `linux/amd64` support is the only host prerequisite for that
workflow. Do not invoke a Docker wrapper from the manifest: VibeSys already
executes both agents and gates inside the selected outer container. Bare
`vibesys --task verus-mpmc-open` works only when the matching `cargo-verus`,
`verus`, Rust, and `vstd` release is installed locally. For local gate iteration,
invoke `runner.py` directly. The runner stages the immutable accuracy or
benchmark crate under `target/` before compiling it, so Cargo never writes into
`.vibesys/`.

VibeSys currently accepts an existing image through `--docker-image`; it does
not build a task-owned Dockerfile. A single command from a clean host therefore
requires publishing this image by immutable registry digest. Until then, the
image build above is a one-time prerequisite.

```bash
python3 .vibesys/tasks/verus-mpmc-open/runner.py check
python3 .vibesys/tasks/verus-mpmc-open/runner.py benchmark \
  --duration-seconds 1 --output-json results.json
```

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
