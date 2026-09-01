use queue_verus_mpmc::MpmcFifo;
use std::collections::HashSet;
use std::env;
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Barrier, Mutex};
use std::thread;
use std::time::{Duration, Instant};

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

fn benchmark(
    duration: Duration,
    capacity: usize,
    producer_count: usize,
    consumer_count: usize,
) -> Result<(), String> {
    if capacity == 0 || producer_count == 0 || consumer_count == 0 {
        return Err("capacity, producers, and consumers must be positive".to_owned());
    }

    let queue = Arc::new(MpmcFifo::new(capacity));
    let stop = Arc::new(AtomicBool::new(false));
    let start = Arc::new(Barrier::new(producer_count + consumer_count + 1));
    let mut producers = Vec::with_capacity(producer_count);
    let mut consumers = Vec::with_capacity(consumer_count);

    for producer in 0..producer_count {
        let queue = Arc::clone(&queue);
        let stop = Arc::clone(&stop);
        let start = Arc::clone(&start);
        producers.push(thread::spawn(move || {
            let mut value = producer as u64;
            let mut completed = 0_u64;
            start.wait();
            while !stop.load(Ordering::Relaxed) {
                match queue.enqueue(value) {
                    Ok(()) => {
                        completed += 1;
                        value = value.wrapping_add(producer_count as u64);
                    }
                    Err(returned) => {
                        value = returned;
                        thread::yield_now();
                    }
                }
            }
            completed
        }));
    }
    for _ in 0..consumer_count {
        let queue = Arc::clone(&queue);
        let stop = Arc::clone(&stop);
        let start = Arc::clone(&start);
        consumers.push(thread::spawn(move || {
            let mut completed = 0_u64;
            start.wait();
            while !stop.load(Ordering::Relaxed) {
                if queue.dequeue().is_some() {
                    completed += 1;
                } else {
                    thread::yield_now();
                }
            }
            completed
        }));
    }

    start.wait();
    let started = Instant::now();
    thread::sleep(duration);
    stop.store(true, Ordering::Relaxed);
    let mut completed = 0_u64;
    for handle in producers {
        completed += handle
            .join()
            .map_err(|_| "producer thread panicked".to_owned())?;
    }
    for handle in consumers {
        completed += handle
            .join()
            .map_err(|_| "consumer thread panicked".to_owned())?;
    }
    let elapsed = started.elapsed().as_secs_f64();
    let throughput = completed as f64 / elapsed;
    if !throughput.is_finite() {
        return Err("benchmark produced a non-finite throughput".to_owned());
    }
    println!("total_ops_per_sec={throughput}");
    Ok(())
}

fn parse_usize(value: Option<String>, name: &str) -> Result<usize, String> {
    value
        .ok_or_else(|| format!("missing {name}"))?
        .parse()
        .map_err(|_| format!("invalid {name}"))
}

fn run() -> Result<(), String> {
    let mut arguments = env::args().skip(1);
    match arguments.next().as_deref() {
        Some("check") if arguments.next().is_none() => check(),
        Some("benchmark") => {
            let duration_ms = parse_usize(arguments.next(), "duration milliseconds")?;
            let capacity = parse_usize(arguments.next(), "capacity")?;
            let producers = parse_usize(arguments.next(), "producer count")?;
            let consumers = parse_usize(arguments.next(), "consumer count")?;
            if arguments.next().is_some() {
                return Err("unexpected benchmark argument".to_owned());
            }
            benchmark(
                Duration::from_millis(duration_ms as u64),
                capacity,
                producers,
                consumers,
            )
        }
        _ => Err(
            "usage: vibesys-verus-mpmc-harness check | benchmark <duration-ms> <capacity> <producers> <consumers>"
                .to_owned(),
        ),
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("FAIL - {error}");
            ExitCode::FAILURE
        }
    }
}
