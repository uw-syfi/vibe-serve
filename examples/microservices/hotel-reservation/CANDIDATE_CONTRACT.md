# Candidate contract

The mutable candidate is DeathStarBench's `hotelReservation` source tree at the
bundle top level and must remain deployable with Docker Compose. Immutable
provenance metadata remains under `reference/`. The frontend listens on HTTP
port 5000.

## Required frontend API

All inputs are URL query parameters. Handlers may accept GET or POST, matching
the upstream application.

- `/hotels`: `inDate`, `outDate`, `lat`, `lon`, optional `locale`.
- `/recommendations`: `require=dis|rate|price`, `lat`, `lon`, optional `locale`.
- `/user`: `username`, `password`.
- `/reservation`: `inDate`, `outDate`, `hotelId`, `customerName`, `username`,
  `password`, and optional `number`.

Search and recommendation responses are strict GeoJSON feature collections.
Feature order is unspecified, but IDs must be unique and each seeded profile's
name, phone number, and coordinates must be exact. Login and reservation return
a JSON object containing exactly `message`; HTTP 200 alone is not success.

The scenario covers the canonical five-service frontend surface used by the
upstream mixed workload. The newer review and attractions routes are outside
the contract because the pinned upstream frontend dials those services without
its Consul resolver and cannot reach them even in the unmodified reference.

Seeded usernames preserve the current upstream grammar. The decimal user suffix
is hex-encoded as bytes: account zero is `Cornell_30` with password
`0000000000`. Hotel IDs are `1` through `80`; room capacities are 200 for IDs
1–6, then 300/250/200 for generated IDs whose remainder modulo three is
1/2/0.

Authentication semantics are checked through `/user`. The pinned reference
still invokes the reservation service after a failed credential check, so the
scenario does not claim that `/reservation` provides an authorization gate.

## Telemetry

During benchmark runs the evaluator injects a Docker Compose override that
replaces the stock `jaeger` service with an OpenTelemetry Collector answering
on the same endpoints, so the application's built-in Jaeger tracing streams
spans to evaluator-owned capture. The candidate must keep exporting spans with
meaningful `service.name` values and must not repoint, remove, or shadow the
`jaeger` service dependency. The injection configuration under
`benchmark/otel/` is trusted input and tamper-checked. Telemetry fails closed
— a run with configured telemetry that produces no spans in the measured
windows fails — but the reports are diagnostic evidence only and never the
scored metric.

## State and ownership

Reservations are persistent and have no public deletion API. Accuracy and
benchmark traffic use collision-resistant future dates, and each evaluation
must start from a disposable Compose project or be torn down with volumes. A
plain process restart is not a clean reset. Evaluator-owned files under
`_evaluator/` and the workload configuration are outside candidate ownership.
When the accuracy runner is given a managed restart hook, it additionally
proves that acknowledged capacity consumption and search visibility survive a
candidate restart; this property is reported as optional otherwise.
