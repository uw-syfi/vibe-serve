mod api;
pub(crate) mod candidate;
pub(crate) mod contract;

pub use api::MpmcFifo;

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
