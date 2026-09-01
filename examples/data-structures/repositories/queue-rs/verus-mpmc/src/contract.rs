use vstd::prelude::*;

verus! {

/// Fixed refinement interface for the candidate-owned sequential storage.
///
/// The public facade serializes these operations. Therefore each successful
/// call is also the linearization point of the corresponding concurrent call.
pub(crate) trait FifoStorage<T>: View<V = Seq<T>> + Sized {
    fn empty() -> (storage: Self)
        ensures
            storage@ == Seq::<T>::empty();

    fn push(&mut self, value: T)
        ensures
            final(self)@ == old(self)@.push(value);

    fn pop(&mut self) -> (result: Option<T>)
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
            };

    fn length(&self) -> (len: usize)
        ensures
            len == self@.len();
}

} // verus!
