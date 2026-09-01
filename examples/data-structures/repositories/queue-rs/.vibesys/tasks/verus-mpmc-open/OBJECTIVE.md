Optimize a formally verified, pure-Rust multi-producer, multi-consumer bounded
FIFO queue.

Headline metric: `total_ops_per_sec` (maximize).

The candidate is the `queue-verus-mpmc` library crate at `verus-mpmc/`. Preserve
this fixed public interface:

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

Internally, `FifoToken<T>` and each operation's `AtomicUpdate` obligation are
proof-only. They connect the task-owned abstract FIFO history to the
candidate's concrete state and erase from the optimized executable.

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

Only files below `verus-mpmc/src/candidate/` are implementer-owned. Keep the
fixed manifest, module wiring, contract, and public facade unchanged. The
accuracy command checks those files, runs both a real Rust compilation and
`cargo verus verify`, then exercises the public Rust API from task-owned code.
The candidate owns the representation, synchronization primitives, invariants,
operation bodies, and the physical points at which it resolves the fixed
logical updates. An implementation may transfer an update through a candidate
invariant to support helping. The fixed facade delegates to the candidate and
does not contain a lock or select a linearization strategy. Verification must
finish with zero errors. Do not use an inconsistent executable path under
ordinary Cargo and verification, or introduce unsound assumptions merely to
make the verifier accept the candidate.

This open-track task evaluates safety and functional refinement. It does not
claim a formal proof of lock-freedom, wait-freedom, starvation freedom, allocator
progress, scheduler fairness, or the Rust/LLVM toolchain.
