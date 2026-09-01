use vstd::prelude::*;
use vstd::rwlock::{ReadHandle, RwLock, RwLockPredicate};

use crate::candidate::Queue;
use crate::contract::FifoStorage;

verus! {

#[allow(dead_code)]
ghost struct QueuePredicate {
    capacity: usize,
}

impl<T> RwLockPredicate<Queue<T>> for QueuePredicate {
    closed spec fn inv(self, state: Queue<T>) -> bool {
        state@.len() <= self.capacity
    }
}

/// A bounded MPMC strict FIFO.
///
/// This minimal certified scaffold fixes exclusive-lock linearization. It is a
/// deliberately conservative seed, not yet the unconstrained logically atomic
/// interface needed to evaluate alternative synchronization algorithms.
pub struct MpmcFifo<T> {
    capacity: usize,
    state: RwLock<Queue<T>, QueuePredicate>,
}

impl<T> MpmcFifo<T> {
    pub closed spec fn capacity_spec(&self) -> usize {
        self.capacity
    }

    #[verifier::type_invariant]
    closed spec fn type_inv(&self) -> bool {
        self.state.pred().capacity == self.capacity
    }

    pub fn new(capacity: usize) -> (queue: Self)
        requires
            capacity > 0,
        ensures
            queue.capacity_spec() == capacity,
    {
        let ghost pred = QueuePredicate { capacity };
        Self { capacity, state: RwLock::new(Queue::empty(), Ghost(pred)) }
    }

    pub fn enqueue(&self, value: T) -> (result: Result<(), T>) {
        proof {
            use_type_invariant(self);
        }
        let (mut state, handle) = self.state.acquire_write();
        if state.length() == self.capacity {
            handle.release_write(state);
            Err(value)
        } else {
            state.push(value);
            handle.release_write(state);
            Ok(())
        }
    }

    pub fn dequeue(&self) -> (result: Option<T>) {
        let (mut state, handle) = self.state.acquire_write();
        let result = state.pop();
        handle.release_write(state);
        result
    }

    pub fn len(&self) -> (len: usize) {
        let handle = self.state.acquire_read();
        let len = ReadHandle::borrow(&handle).length();
        handle.release_read();
        len
    }

    pub fn is_empty(&self) -> (empty: bool) {
        self.len() == 0
    }
}

} // verus!
