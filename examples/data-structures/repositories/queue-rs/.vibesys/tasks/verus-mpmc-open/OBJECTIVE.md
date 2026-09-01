Optimize a formally verified, pure-Rust multi-producer, multi-consumer bounded
FIFO queue.

Headline metric: `total_ops_per_sec` (maximize).

The candidate is the `queue-verus-mpmc` library crate at `verus-mpmc/`. Preserve
this public interface:

```rust
pub struct MpmcFifo<T>;

impl<T> MpmcFifo<T> {
    pub fn new(capacity: usize) -> Self;
    pub fn enqueue(&self, value: T) -> Result<(), T>;
    pub fn dequeue(&self) -> Option<T>;
    pub fn len(&self) -> usize;
    pub fn is_empty(&self) -> bool;
}
```

The queue has exact linearizable bounded-FIFO semantics. Every completed
operation takes effect at one point between its invocation and return:

- `new(capacity)` requires `capacity > 0` and creates an empty queue with that
  fixed item capacity.
- `enqueue(value)` returns `Ok(())` exactly when the queue is below capacity at
  its linearization point and appends `value` to the single global FIFO order.
  At capacity it returns `Err(value)` and leaves the queue unchanged.
- `dequeue()` returns and removes the oldest value exactly when one exists. It
  returns `None` exactly when the queue is empty and leaves the queue unchanged.
- `len()` returns the exact abstract queue length at its linearization point;
  `is_empty()` is equivalent to `len() == 0` at its own linearization point.

Unlike the existing native MPMC task, this task does not permit capacity
reservation before publication. In particular, an enqueue may not make a
concurrent enqueue observe full while a concurrent dequeue still observes
empty. Values are never lost, duplicated, fabricated, or reordered.

Keep `[package.metadata.verus].verify = true`. The accuracy command runs both a
real Rust compilation and `cargo verus verify`, then exercises the public Rust
API from task-owned code. Executable representation, synchronization, ghost
state, lemmas, and proof organization remain candidate-owned. Verification must
finish with zero errors. Do not use an inconsistent executable path under
ordinary Cargo and verification, or introduce unsound assumptions merely to
make the verifier accept the candidate.

This open-track task evaluates safety and functional refinement. It does not
claim a formal proof of lock-freedom, wait-freedom, starvation freedom, allocator
progress, scheduler fairness, or the Rust/LLVM toolchain.
