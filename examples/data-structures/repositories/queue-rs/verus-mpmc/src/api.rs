use vstd::atomic::{AtomicUpdate, Commit};
use vstd::invariant::*;
use vstd::open_atomic_invariant_in_proof;
use vstd::iset::ISet;
use vstd::prelude::*;

use crate::candidate::Queue;
use crate::contract::{FifoToken, QueueConstruction};

verus! {

pub(crate) struct ClientTokenInv<T>(core::marker::PhantomData<T>);

impl<T> InvariantPredicate<(vstd::resource::Loc, usize), FifoToken<T>> for ClientTokenInv<T> {
    open spec fn inv(constant: (vstd::resource::Loc, usize), token: FifoToken<T>) -> bool {
        &&& token.id() == constant.0
        &&& token.contents().len() <= constant.1
    }
}

pub open spec const FIFO_CLIENT_INV: int = 734_201;

/// Public queue handle. Its tracked invariant is erased and owns no runtime
/// synchronization. All executable synchronization remains in `inner`.
pub struct MpmcFifo<T> {
    pub(crate) inner: Queue<T>,
    pub(crate) token: Tracked<AtomicInvariant<(vstd::resource::Loc, usize), FifoToken<T>, ClientTokenInv<T>>>,
}

impl<T> MpmcFifo<T> {
    pub closed spec fn wf(&self) -> bool {
        &&& self.inner.wf()
        &&& self.token@.constant() == (self.inner.token_id(), self.inner.capacity())
        &&& self.token@.namespace() == FIFO_CLIENT_INV
    }

    pub closed spec fn token_id(&self) -> vstd::resource::Loc {
        self.inner.token_id()
    }

    pub closed spec fn capacity(&self) -> usize {
        self.inner.capacity()
    }

    pub(crate) proof fn expose_model(&self)
        requires
            self.wf(),
        ensures
            self.inner.wf(),
            self.token_id() == self.inner.token_id(),
            self.capacity() == self.inner.capacity(),
    {
        assert(self.token_id() == self.inner.token_id());
        assert(self.capacity() == self.inner.capacity());
    }

}

pub(crate) type EnqueueCommit<T> = Commit<(FifoToken<T>, Ghost<Result<(), T>>)>;
pub(crate) type DequeueCommit<T> = Commit<(FifoToken<T>, Ghost<Option<T>>)>;
pub(crate) type LenCommit<T> = Commit<(FifoToken<T>, Ghost<usize>)>;

pub type EnqueueAU<T> = AtomicUpdate<FifoToken<T>, EnqueueCommit<T>, EnqueuePred<T>>;
pub type DequeueAU<T> = AtomicUpdate<FifoToken<T>, DequeueCommit<T>, DequeuePred<T>>;
pub type LenAU<T> = AtomicUpdate<FifoToken<T>, LenCommit<T>, LenPred<T>>;

/// Fixed bridge from logical atomicity to candidate code. Implementations may
/// choose where to resolve or transfer the AU, but cannot weaken this contract.
pub(crate) trait QueueOperations<T> {
    fn enqueue_op(
        queue: &MpmcFifo<T>,
        value: T,
        au: Tracked<EnqueueAU<T>>,
    ) -> (result: Result<(), T>)
        requires
            queue.wf(),
            au@.pred().args(queue, value),
        ensures
            au@.resolves(),
            result == au@.output()@.1@;

    fn dequeue_op(queue: &MpmcFifo<T>, au: Tracked<DequeueAU<T>>) -> (result: Option<T>)
        requires
            queue.wf(),
            au@.pred().args(queue),
        ensures
            au@.resolves(),
            result == au@.output()@.1@;

    fn len_op(queue: &MpmcFifo<T>, au: Tracked<LenAU<T>>) -> (result: usize)
        requires
            queue.wf(),
            au@.pred().args(queue),
        ensures
            au@.resolves(),
            result == au@.output()@.1@;
}

impl<T> MpmcFifo<T> {
pub fn new(capacity: usize) -> (out: Self)
    requires
        capacity > 0,
    ensures
        out.wf(),
        out.capacity() == capacity,
{
    let (inner, Tracked(token)) = Queue::create(capacity);
    let ghost constant = (token.id(), capacity);
    let tracked token = AtomicInvariant::new(constant, token, FIFO_CLIENT_INV);
    MpmcFifo { inner, token: Tracked(token) }
}

pub fn enqueue(&self, value: T) -> (result: Result<(), T>)
    requires self.wf(),
{
    let Tracked(credit) = vstd::invariant::create_open_invariant_credit();
    enqueue_atomic(self, value) atomically |update| {
        open_atomic_invariant!(credit => self.token.borrow() => token => {
            let tracked updated: EnqueueCommit<T> = update(token);
            let tracked (next, Ghost(_result)) = updated.get();
            token = next;
        });
    }
}

pub fn dequeue(&self) -> (result: Option<T>)
    requires self.wf(),
{
    let Tracked(credit) = vstd::invariant::create_open_invariant_credit();
    dequeue_atomic(self) atomically |update| {
        open_atomic_invariant!(credit => self.token.borrow() => token => {
            let tracked updated: DequeueCommit<T> = update(token);
            let tracked (next, Ghost(_result)) = updated.get();
            token = next;
        });
    }
}

pub fn len(&self) -> (result: usize)
    requires self.wf(),
{
    let Tracked(credit) = vstd::invariant::create_open_invariant_credit();
    len_atomic(self) atomically |update| {
        open_atomic_invariant!(credit => self.token.borrow() => token => {
            let tracked updated: LenCommit<T> = update(token);
            let tracked (next, Ghost(_result)) = updated.get();
            token = next;
        });
    }
}

pub fn is_empty(&self) -> (result: bool)
    requires self.wf(),
{
    self.len() == 0
}
}

/// Logically atomic bounded FIFO enqueue.
///
/// The candidate receives the atomic update and may commit it at any physical
/// step, or transfer it through a candidate invariant for helping.
pub(crate) fn enqueue_atomic<T>(queue: &MpmcFifo<T>, value: T) -> (result: Result<(), T>)
    atomically (atomic_update) {
        type EnqueuePred,
        (old_token: FifoToken<T>) -> (commit: EnqueueCommit<T>),
        requires
            old_token.id() == queue.token_id(),
            old_token.contents().len() <= queue.capacity(),
        ensures
            commit@.0.id() == old_token.id(),
            match commit@.1@ {
                Ok(()) => {
                    &&& old_token.contents().len() < queue.capacity()
                    &&& commit@.0.contents() == old_token.contents().push(value)
                },
                Err(returned) => {
                    &&& returned == value
                    &&& old_token.contents().len() == queue.capacity()
                    &&& commit@.0.contents() == old_token.contents()
                },
            },
        outer_mask ISet::<int>::full(),
        inner_mask none,
    },
    requires queue.wf(),
    ensures result == commit@.1@,
{
    <Queue<T> as QueueOperations<T>>::enqueue_op(queue, value, Tracked(atomic_update))
}

/// Logically atomic strict FIFO dequeue.
pub(crate) fn dequeue_atomic<T>(queue: &MpmcFifo<T>) -> (result: Option<T>)
    atomically (atomic_update) {
        type DequeuePred,
        (old_token: FifoToken<T>) -> (commit: DequeueCommit<T>),
        requires
            old_token.id() == queue.token_id(),
            old_token.contents().len() <= queue.capacity(),
        ensures
            commit@.0.id() == old_token.id(),
            match commit@.1@ {
                Some(value) => {
                    &&& old_token.contents().len() > 0
                    &&& value == old_token.contents()[0]
                    &&& commit@.0.contents()
                        == old_token.contents().subrange(1, old_token.contents().len() as int)
                },
                None => {
                    &&& old_token.contents().len() == 0
                    &&& commit@.0.contents() == old_token.contents()
                },
            },
        outer_mask ISet::<int>::full(),
        inner_mask none,
    },
    requires queue.wf(),
    ensures result == commit@.1@,
{
    <Queue<T> as QueueOperations<T>>::dequeue_op(queue, Tracked(atomic_update))
}

/// Logically atomic size observation.
pub(crate) fn len_atomic<T>(queue: &MpmcFifo<T>) -> (result: usize)
    atomically (atomic_update) {
        type LenPred,
        (old_token: FifoToken<T>) -> (commit: LenCommit<T>),
        requires
            old_token.id() == queue.token_id(),
            old_token.contents().len() <= queue.capacity(),
        ensures
            commit@.0 == old_token,
            commit@.1@ == old_token.contents().len(),
        outer_mask ISet::<int>::full(),
        inner_mask none,
    },
    requires queue.wf(),
    ensures result == commit@.1@,
{
    <Queue<T> as QueueOperations<T>>::len_op(queue, Tracked(atomic_update))
}

} // verus!
