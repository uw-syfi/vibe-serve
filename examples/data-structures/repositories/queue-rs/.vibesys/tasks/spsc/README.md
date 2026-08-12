# Queue SPSC Task

This task targets a single-producer, single-consumer bounded FIFO queue.
Candidates implement the evaluator's copying C ABI and export it from
`./queue-candidate.so`.

The repository provides an editable `src/lib.rs` with an intentionally naive
Rust candidate using one mutex and `VecDeque`. Build and validate it from the
repository root:

    make
    vibesys-queue check --workspace "$PWD" --scenario spsc
    vibesys-queue benchmark --workspace "$PWD" --scenario spsc --duration 1s --warmup 0s

The repository implementation is untrusted and may be replaced with any implementation that
exports the same ABI. `--use-reference` only self-tests the evaluator's internal
model; it is not the optimization starting point. The manifest benchmark uses
three repetitions and reports their median as `total_ops_per_sec`.
