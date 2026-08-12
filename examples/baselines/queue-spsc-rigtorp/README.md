# Rigtorp SPSC baseline

This is the comparison baseline for the `spsc` task in the `queue-rs`
optimization repository. It
vendors the unmodified upstream
[`SPSCQueue.h`](https://github.com/rigtorp/SPSCQueue/blob/1053918dbd251fbff69b24ef27fa5d51c29ec2af/include/rigtorp/SPSCQueue.h)
at commit `1053918dbd251fbff69b24ef27fa5d51c29ec2af` and directly instantiates
`rigtorp::SPSCQueue` behind the VibeSys copying byte-value ABI.

- one preallocated circular buffer with one slack slot;
- producer-owned and consumer-owned indices on separate cache lines;
- cached copies of the remote index to reduce coherency traffic; and
- release publication paired with acquire observation.

The thin adapter queues descriptors that point into a separate, fixed-stride
payload arena. Both the Rigtorp queue and payload arena are allocated once at
queue creation, so the ABI bridge adds no per-operation heap allocation.

The vendored header is byte-for-byte identical to that upstream revision and
retains its license header. Rigtorp's MIT license is also in `LICENSE.rigtorp`.

This directory is deliberately outside the `queue-rs` repository. VibeSys does
not include it in optimization workspaces, so optimization agents cannot inspect
or reuse the implementation.

From this directory:

```bash
make
go -C ../../../resources/evaluators/queue run . check \
  --workspace ../../../examples/baselines/queue-spsc-rigtorp --scenario spsc
go -C ../../../resources/evaluators/queue run . benchmark \
  --workspace ../../../examples/baselines/queue-spsc-rigtorp --scenario spsc \
  --repetitions 3
```
