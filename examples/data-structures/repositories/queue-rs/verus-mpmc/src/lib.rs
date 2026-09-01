use std::collections::VecDeque;
use vstd::prelude::*;
use vstd::rwlock::{ReadHandle, RwLock, RwLockPredicate};

verus! {

/// A sequential FIFO whose executable operations refine an immutable `Seq<T>`.
///
/// This type is kept separate from the lock so the strict FIFO property is a
/// small, reusable verification obligation. `MpmcFifo` serializes these exact
/// operations with a verified reader-writer lock.
struct SequentialFifo<T> {
    entries: VecDeque<T>,
}

impl<T> View for SequentialFifo<T> {
    type V = Seq<T>;

    closed spec fn view(&self) -> Seq<T> {
        self.entries@
    }
}

impl<T> SequentialFifo<T> {
    fn new() -> (fifo: Self)
        ensures
            fifo@ == Seq::<T>::empty(),
    {
        Self { entries: VecDeque::new() }
    }

    fn enqueue(&mut self, value: T)
        ensures
            final(self)@ == old(self)@.push(value),
    {
        self.entries.push_back(value);
    }

    fn dequeue(&mut self) -> (result: Option<T>)
        ensures
            match result {
                Some(value) => {
                    &&& old(self)@.len() > 0
                    &&& value == old(self)@[0]
                    &&& final(self)@ == old(self)@.subrange(1, old(self)@.len() as int)
                },
                None => {
                    &&& old(self)@.len() == 0
                    &&& final(self)@ == old(self)@
                },
            },
    {
        self.entries.pop_front()
    }

    fn len(&self) -> (len: usize)
        ensures
            len == self@.len(),
    {
        self.entries.len()
    }
}

struct QueueState<T> {
    fifo: SequentialFifo<T>,
}

/// The invariant stored in the lock. It ties the executable bounded queue to
/// its construction-time capacity while `SequentialFifo` supplies the exact
/// abstract sequence.
#[allow(dead_code)]
ghost struct QueuePredicate {
    capacity: usize,
}

impl<T> RwLockPredicate<QueueState<T>> for QueuePredicate {
    closed spec fn inv(self, state: QueueState<T>) -> bool {
        state.fifo@.len() <= self.capacity
    }
}

/// A bounded multi-producer, multi-consumer strict FIFO.
///
/// All mutation happens while holding an exclusive lock. Successful enqueue
/// appends to the abstract sequence; successful dequeue removes index zero.
/// Lock acquisition may spin, so this prototype establishes safety and strict
/// FIFO behavior but does not claim starvation freedom or termination.
pub struct MpmcFifo<T> {
    capacity: usize,
    state: RwLock<QueueState<T>, QueuePredicate>,
}

impl<T> MpmcFifo<T> {
    pub closed spec fn capacity_spec(&self) -> usize {
        self.capacity
    }

    #[verifier::type_invariant]
    closed spec fn type_inv(&self) -> bool {
        self.state.pred().capacity == self.capacity
    }

    /// Creates an empty queue with space for at most `capacity` elements.
    pub fn new(capacity: usize) -> (queue: Self)
        requires
            capacity > 0,
        ensures
            queue.capacity_spec() == capacity,
    {
        let state = QueueState { fifo: SequentialFifo::new() };
        let ghost pred = QueuePredicate { capacity };
        Self { capacity, state: RwLock::new(state, Ghost(pred)) }
    }

    /// Appends `value`, or returns it unchanged when the queue is full.
    pub fn enqueue(&self, value: T) -> (result: Result<(), T>) {
        proof {
            use_type_invariant(self);
        }
        let (mut state, handle) = self.state.acquire_write();
        if state.fifo.len() == self.capacity {
            handle.release_write(state);
            Err(value)
        } else {
            state.fifo.enqueue(value);
            handle.release_write(state);
            Ok(())
        }
    }

    /// Removes and returns the oldest queued element, if one exists.
    pub fn dequeue(&self) -> (result: Option<T>) {
        let (mut state, handle) = self.state.acquire_write();
        let result = state.fifo.dequeue();
        handle.release_write(state);
        result
    }

    /// Returns the number of elements observed while holding a shared lock.
    pub fn len(&self) -> (len: usize) {
        let handle = self.state.acquire_read();
        let len = ReadHandle::borrow(&handle).fifo.len();
        handle.release_read();
        len
    }

    /// Returns whether the queue was empty at the observation point.
    pub fn is_empty(&self) -> (empty: bool) {
        self.len() == 0
    }
}

} // verus!

#[cfg(test)]
mod tests {
    use super::MpmcFifo;
    use std::collections::HashSet;
    use std::sync::Arc;
    use std::thread;

    #[test]
    fn bounded_fifo_contract() {
        let queue = MpmcFifo::new(2);

        assert!(queue.is_empty());
        assert_eq!(queue.enqueue(10), Ok(()));
        assert_eq!(queue.enqueue(20), Ok(()));
        assert_eq!(queue.enqueue(30), Err(30));
        assert_eq!(queue.len(), 2);
        assert_eq!(queue.dequeue(), Some(10));
        assert_eq!(queue.dequeue(), Some(20));
        assert_eq!(queue.dequeue(), None);
    }

    #[test]
    fn multiple_producers_and_consumers_preserve_values() {
        const PRODUCERS: usize = 4;
        const CONSUMERS: usize = 4;
        const VALUES_PER_PRODUCER: usize = 250;
        const TOTAL: usize = PRODUCERS * VALUES_PER_PRODUCER;

        let queue = Arc::new(MpmcFifo::new(TOTAL));
        let producers = (0..PRODUCERS)
            .map(|producer| {
                let queue = Arc::clone(&queue);
                thread::spawn(move || {
                    for offset in 0..VALUES_PER_PRODUCER {
                        let value = producer * VALUES_PER_PRODUCER + offset;
                        assert_eq!(queue.enqueue(value), Ok(()));
                    }
                })
            })
            .collect::<Vec<_>>();

        for producer in producers {
            producer.join().unwrap();
        }

        let consumers = (0..CONSUMERS)
            .map(|_| {
                let queue = Arc::clone(&queue);
                thread::spawn(move || {
                    let mut values = Vec::new();
                    while values.len() < TOTAL / CONSUMERS {
                        if let Some(value) = queue.dequeue() {
                            values.push(value);
                        }
                    }
                    values
                })
            })
            .collect::<Vec<_>>();

        let values = consumers
            .into_iter()
            .flat_map(|consumer| consumer.join().unwrap())
            .collect::<HashSet<_>>();
        assert_eq!(values.len(), TOTAL);
        assert_eq!(values, (0..TOTAL).collect());
        assert!(queue.is_empty());
    }
}
