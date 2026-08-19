# vs-evaluator-protocol

Framework-side reader for the VibeSys evaluator result protocol, version 2. The
protocol itself, its fixtures, and the evaluator-side SDKs live in
`sdk/vs-evaluator/`; this library is the only implementation the framework uses
to consume an evaluator's record stream.

The public API exported from `vs_evaluator_protocol` has three groups:

- `Hello`, `Result`, `ErrorRecord`, `MetricSpec`, and the `Record` union define
  the records, with `PROTOCOL_VERSION` naming the version this reader
  implements. `MetricSpec.required` (default `true`) says whether every
  successful run must report the metric; an optional metric is still declared,
  so it can never be reported under a name the reader has not seen.
- `parse_records` turns stream text into typed records, one per non-blank line.
  `read_measurement` consumes records in order and returns one `Measurement`:
  the declared metrics plus either the measured row or a failure message. An
  `error` may replace the `hello` entirely, for an evaluator that fails before
  its configuration tells it what it measures; such a `Measurement` has
  `metrics is None`. A measured row always carries the declaration it was
  validated against.
- `check_objectives` verifies that a task's objectives are declared by the
  evaluator *and* declared required. It is separate from `read_measurement`
  because objectives belong to the task; the evaluator never learns which
  metrics are optimized. A task cannot rank on an optional metric, since a
  successful run may omit it and the frontier would silently lose the round.

Every rejection raises `ProtocolError` carrying a `ReasonCode` from the shared
contract and a message naming the offending record and key. Record models
reject unknown keys and type coercion; Pydantic failures are translated, never
surfaced. `read_measurement` loops over records rather than indexing a
fully-parsed stream, so a transport that delivers records incrementally can
reuse it unchanged.

The reader also accepts version 1 streams, reading them as declaring every
metric required. That support is transitional: it exists only until the Go
evaluators in this repository are rebuilt against an SDK that emits version 2,
and the code carrying it is marked for deletion in `records.py`.

The library does not read or write files, run evaluators, or score
measurements.
