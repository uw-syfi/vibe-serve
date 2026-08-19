# vs-evaluator-protocol

Framework-side reader for the VibeSys evaluator result protocol, version 1. The
protocol itself, its fixtures, and the evaluator-side SDKs live in
`sdk/vs-evaluator/`; this library is the only implementation the framework uses
to consume an evaluator's record stream.

The public API exported from `vs_evaluator_protocol` has three groups:

- `Hello`, `Result`, `ErrorRecord`, `MetricSpec`, and the `Record` union define
  the records, with `PROTOCOL_VERSION` naming the version this reader
  implements.
- `parse_records` turns stream text into typed records, one per non-blank line.
  `read_measurement` consumes records in order and returns one `Measurement`:
  the declared metrics plus either the measured row or a failure message.
- `check_objectives` verifies that a task's objectives are declared by the
  evaluator. It is separate from `read_measurement` because objectives belong
  to the task; the evaluator never learns which metrics are optimized.

Every rejection raises `ProtocolError` carrying a `ReasonCode` from the shared
contract and a message naming the offending record and key. Record models
reject unknown keys and type coercion; Pydantic failures are translated, never
surfaced. `read_measurement` loops over records rather than indexing a
fully-parsed stream, so a transport that delivers records incrementally can
reuse it unchanged.

The library does not read or write files, run evaluators, or score
measurements.
