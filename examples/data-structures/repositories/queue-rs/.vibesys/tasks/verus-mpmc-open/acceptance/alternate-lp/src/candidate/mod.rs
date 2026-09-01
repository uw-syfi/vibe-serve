use std::collections::VecDeque;
use vstd::atomic::*;
use vstd::prelude::*;
use vstd::resource::ghost_var::GhostVarAuth;
use vstd::resource::Loc;
use vstd::rwlock::{RwLock, RwLockPredicate};

use crate::api::{DequeueAU, EnqueueAU, LenAU, MpmcFifo, QueueOperations};
use crate::contract::{FifoToken, QueueConstruction};

verus! {

pub(crate) struct State<T> {
    pub(crate) entries: VecDeque<T>,
    pub(crate) auth: Tracked<GhostVarAuth<Seq<T>>>,
}

pub(crate) struct QueuePredicate {
    pub(crate) capacity: usize,
    pub(crate) token_id: Loc,
}

impl<T> RwLockPredicate<State<T>> for QueuePredicate {
    closed spec fn inv(self, state: State<T>) -> bool {
        &&& state.auth@.id() == self.token_id
        &&& state.auth@@ == state.entries@
        &&& state.entries@.len() <= self.capacity
    }
}

pub(crate) struct Queue<T> {
    pub(crate) capacity: usize,
    pub(crate) state: RwLock<State<T>, QueuePredicate>,
}

impl<T> QueueConstruction<T> for Queue<T> {
    open spec fn wf(&self) -> bool {
        &&& self.state.pred().capacity == self.capacity
        &&& self.state.pred().token_id == self.token_id()
    }

    open spec fn token_id(&self) -> Loc {
        self.state.pred().token_id
    }

    open spec fn capacity(&self) -> usize {
        self.capacity
    }

    fn create(capacity: usize) -> (out: (Self, Tracked<FifoToken<T>>)) {
        let tracked (auth, token) = GhostVarAuth::new(Seq::<T>::empty());
        let ghost token_id = token.id();
        let ghost pred = QueuePredicate { capacity, token_id };
        let state = State { entries: VecDeque::new(), auth: Tracked(auth) };
        let queue = Queue { capacity, state: RwLock::new(state, Ghost(pred)) };
        (queue, Tracked(FifoToken { state: token }))
    }
}

impl<T> QueueOperations<T> for Queue<T> {
fn enqueue_op(
    queue: &MpmcFifo<T>, value: T, Tracked(au): Tracked<EnqueueAU<T>>,
) -> (result: Result<(), T>) {
    proof { queue.expose_model(); }
    let (mut state, handle) = queue.inner.state.acquire_write();
    if state.entries.len() == queue.inner.capacity {
        proof {
            try_open_atomic_update!(au, mut token => {
                state.auth.borrow().agree(&token.state);
                Tracked(Commit((token, Ghost(Err(value)))))
            });
        }
        handle.release_write(state);
        Err(value)
    } else {
        proof {
            assert(state.entries@.len() < queue.inner.capacity);
            try_open_atomic_update!(au, mut token => {
                state.auth.borrow().agree(&token.state);
                state.auth.borrow_mut().update(
                    &mut token.state,
                    state.entries@.push(value),
                );
                Tracked(Commit((token, Ghost(Ok(())))))
            });
        }
        state.entries.push_back(value);
        proof {
            assert(state.entries@.len() <= queue.inner.capacity);
        }
        handle.release_write(state);
        Ok(())
    }
}

fn dequeue_op(
    queue: &MpmcFifo<T>, Tracked(au): Tracked<DequeueAU<T>>,
) -> (result: Option<T>) {
    proof { queue.expose_model(); }
    let (mut state, handle) = queue.inner.state.acquire_write();
    if state.entries.len() == 0 {
        proof {
            try_open_atomic_update!(au, token => {
                state.auth.borrow().agree(&token.state);
                Tracked(Commit((token, Ghost(None))))
            });
        }
        handle.release_write(state);
        None
    } else {
        proof {
            try_open_atomic_update!(au, mut token => {
                state.auth.borrow().agree(&token.state);
                state.auth.borrow_mut().update(
                    &mut token.state,
                    state.entries@.subrange(1, state.entries@.len() as int),
                );
                Tracked(Commit((token, Ghost(Some(state.entries@[0])))))
            });
        }
        let result = state.entries.pop_front();
        handle.release_write(state);
        result
    }
}

fn len_op(
    queue: &MpmcFifo<T>, Tracked(au): Tracked<LenAU<T>>,
) -> (result: usize) {
    proof { queue.expose_model(); }
    let handle = queue.inner.state.acquire_read();
    let state = handle.borrow();
    let result = state.entries.len();
    proof {
        try_open_atomic_update!(au, token => {
            state.auth.borrow().agree(&token.state);
            Tracked(Commit((token, Ghost(result))))
        });
    }
    handle.release_read();
    result
}
}

} // verus!
