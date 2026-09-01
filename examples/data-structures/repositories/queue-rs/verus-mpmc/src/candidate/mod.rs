use std::collections::VecDeque;
use vstd::prelude::*;

use crate::contract::FifoStorage;

verus! {

/// Candidate-owned sequential representation and its refinement proof.
///
/// Agents may replace this file and add modules below `candidate/`, while the
/// public API and the `FifoStorage` contract remain fixed.
pub(crate) struct Queue<T> {
    entries: VecDeque<T>,
}

impl<T> View for Queue<T> {
    type V = Seq<T>;

    closed spec fn view(&self) -> Seq<T> {
        self.entries@
    }
}

impl<T> FifoStorage<T> for Queue<T> {
    fn empty() -> (storage: Self) {
        Self { entries: VecDeque::new() }
    }

    fn push(&mut self, value: T) {
        self.entries.push_back(value);
    }

    fn pop(&mut self) -> (result: Option<T>) {
        self.entries.pop_front()
    }

    fn length(&self) -> (len: usize) {
        self.entries.len()
    }
}

} // verus!
