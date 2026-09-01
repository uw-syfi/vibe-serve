use queue_verus_mpmc::MpmcFifo;
use std::collections::HashSet;
use std::process::ExitCode;
use std::sync::{Arc, Barrier, Mutex};
use std::thread;

fn sequential_contract() -> Result<(), String> {
    let queue = MpmcFifo::new(2);
    if !queue.is_empty() || queue.len() != 0 || queue.dequeue().is_some() {
        return Err("a new queue must be empty".to_owned());
    }
    if queue.enqueue(10_u64) != Ok(()) || queue.enqueue(20_u64) != Ok(()) {
        return Err("enqueue below capacity must succeed".to_owned());
    }
    if queue.len() != 2 || queue.is_empty() {
        return Err("length must equal the number of queued values".to_owned());
    }
    if queue.enqueue(30_u64) != Err(30_u64) {
        return Err("enqueue at capacity must return the input value".to_owned());
    }
    if queue.dequeue() != Some(10_u64) || queue.dequeue() != Some(20_u64) {
        return Err("dequeue must preserve FIFO order".to_owned());
    }
    if queue.dequeue().is_some() || !queue.is_empty() || queue.len() != 0 {
        return Err("draining the queue must restore the empty state".to_owned());
    }
    Ok(())
}

fn producer_order_contract() -> Result<(), String> {
    const PRODUCERS: usize = 4;
    const ITEMS_PER_PRODUCER: usize = 256;
    let queue = Arc::new(MpmcFifo::new(PRODUCERS * ITEMS_PER_PRODUCER));
    let start = Arc::new(Barrier::new(PRODUCERS));
    let mut handles = Vec::with_capacity(PRODUCERS);

    for producer in 0..PRODUCERS {
        let queue = Arc::clone(&queue);
        let start = Arc::clone(&start);
        handles.push(thread::spawn(move || {
            start.wait();
            for sequence in 0..ITEMS_PER_PRODUCER {
                let value = ((producer as u64) << 32) | sequence as u64;
                queue
                    .enqueue(value)
                    .map_err(|_| "enqueue unexpectedly reported full".to_owned())?;
            }
            Ok::<(), String>(())
        }));
    }
    for handle in handles {
        handle
            .join()
            .map_err(|_| "producer thread panicked".to_owned())??;
    }

    if queue.len() != PRODUCERS * ITEMS_PER_PRODUCER {
        return Err("concurrent enqueue lost or fabricated an item".to_owned());
    }
    let mut next = [0_usize; PRODUCERS];
    for _ in 0..PRODUCERS * ITEMS_PER_PRODUCER {
        let value = queue
            .dequeue()
            .ok_or_else(|| "queue became empty before every value was returned".to_owned())?;
        let producer = (value >> 32) as usize;
        let sequence = value as u32 as usize;
        if producer >= PRODUCERS || sequence != next[producer] {
            return Err("global FIFO order violated a producer's real-time order".to_owned());
        }
        next[producer] += 1;
    }
    if next != [ITEMS_PER_PRODUCER; PRODUCERS] || queue.dequeue().is_some() {
        return Err("concurrent enqueue values were not returned exactly once".to_owned());
    }
    Ok(())
}

fn consumer_conservation_contract() -> Result<(), String> {
    const CONSUMERS: usize = 4;
    const ITEM_COUNT: usize = 1024;
    let queue = Arc::new(MpmcFifo::new(ITEM_COUNT));
    for value in 0..ITEM_COUNT as u64 {
        queue
            .enqueue(value)
            .map_err(|_| "prefill unexpectedly reported full".to_owned())?;
    }

    let observed = Arc::new(Mutex::new(Vec::with_capacity(ITEM_COUNT)));
    let start = Arc::new(Barrier::new(CONSUMERS));
    let mut handles = Vec::with_capacity(CONSUMERS);
    for _ in 0..CONSUMERS {
        let queue = Arc::clone(&queue);
        let observed = Arc::clone(&observed);
        let start = Arc::clone(&start);
        handles.push(thread::spawn(move || {
            start.wait();
            let mut local = Vec::with_capacity(ITEM_COUNT / CONSUMERS);
            for _ in 0..ITEM_COUNT / CONSUMERS {
                local.push(
                    queue
                        .dequeue()
                        .ok_or_else(|| "consumer observed a premature empty queue".to_owned())?,
                );
            }
            observed
                .lock()
                .map_err(|_| "result lock was poisoned".to_owned())?
                .extend(local);
            Ok::<(), String>(())
        }));
    }
    for handle in handles {
        handle
            .join()
            .map_err(|_| "consumer thread panicked".to_owned())??;
    }

    let values = observed
        .lock()
        .map_err(|_| "result lock was poisoned".to_owned())?;
    let unique: HashSet<_> = values.iter().copied().collect();
    if values.len() != ITEM_COUNT
        || unique.len() != ITEM_COUNT
        || unique.iter().any(|value| *value >= ITEM_COUNT as u64)
        || !queue.is_empty()
    {
        return Err("concurrent dequeue lost, duplicated, or fabricated a value".to_owned());
    }
    Ok(())
}

fn check() -> Result<(), String> {
    sequential_contract()?;
    producer_order_contract()?;
    consumer_conservation_contract()?;
    println!("PASS - pure-Rust exact-linearizable MPMC FIFO checks");
    Ok(())
}

fn main() -> ExitCode {
    match check() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("FAIL - {error}");
            ExitCode::FAILURE
        }
    }
}
