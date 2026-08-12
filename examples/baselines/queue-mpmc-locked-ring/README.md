# Locked-ring MPMC baseline

This is the conservative control baseline for the `mpmc` task in the `queue-rs`
optimization repository. It stores copied
values in a fixed, preallocated ring and serializes reservation, copying, and
publication with one mutex. The MPMC checker permits reservation before
publication, but this implementation makes both externally atomic. It is a
valid simple implementation of the broader reservation-aware contract.

The directory is outside the `queue-rs` repository and is not included in
optimization workspaces.

```bash
make
go -C ../../../resources/evaluators/queue run . check \
  --workspace ../../../examples/baselines/queue-mpmc-locked-ring --scenario mpmc \
  --operations 24 --trials 100
go -C ../../../resources/evaluators/queue run . benchmark \
  --workspace ../../../examples/baselines/queue-mpmc-locked-ring --scenario mpmc \
  --repetitions 3
```
