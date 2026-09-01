use vstd::prelude::*;
use vstd::resource::ghost_var::GhostVar;
use vstd::resource::Loc;

verus! {

/// The client-owned half of the abstract FIFO state.
///
/// The fixed facade keeps this token in an erased client-side invariant and
/// supplies it at calls to the logically atomic API. The candidate keeps the
/// authoritative half in an invariant tied to its concrete representation.
pub tracked struct FifoToken<T> {
    pub state: GhostVar<Seq<T>>,
}

impl<T> FifoToken<T> {
    pub open spec fn id(self) -> Loc {
        self.state.id()
    }

    pub open spec fn contents(self) -> Seq<T> {
        self.state@
    }
}

/// Fixed construction contract for a candidate-owned concurrent queue.
///
/// Synchronization, representation, invariants, and operation bodies remain
/// candidate-owned. The fixed API only relies on these abstract facts.
pub(crate) trait QueueConstruction<T>: Sized {
    spec fn wf(&self) -> bool;

    spec fn token_id(&self) -> Loc;

    spec fn capacity(&self) -> usize;

    fn create(capacity: usize) -> (out: (Self, Tracked<FifoToken<T>>))
        requires
            capacity > 0,
        ensures
            out.0.wf(),
            out.0.capacity() == capacity,
            out.1@.id() == out.0.token_id(),
            out.1@.contents() == Seq::<T>::empty();
}

} // verus!
