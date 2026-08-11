# Hotel Reservation

This scenario optimizes DeathStarBench's Go/gRPC Hotel Reservation stack under
its canonical search-heavy mixed workload. Correctness is checked independently
through the shared microservice evaluator, including strict profile and
recommendation semantics, randomized authentication cases, negative protocol
cases, and stateful room-capacity transitions.

## Prepare the candidate

DeathStarBench is pinned by the existing repository submodule. Its upstream
default branch is `master`; the pinned revision already matches the latest
remote `master` at the time this scenario was added.

```bash
git submodule update --init \
  examples/microservices/social-network-read-timeline/3rd_party/deathstarbench
examples/microservices/hotel-reservation/scripts/materialize-reference.sh
```

The materialized source is intentionally ignored by this repository. VibeSys
copies it into the separate experiment workspace, where optimization commits
belong; the pull request for this scenario contains only evaluator and bundle
code.

## Validate locally

From the repository root, let the evaluator own candidate startup and cleanup:

```bash
go -C examples/evaluators/microservice run ./cmd/servicebench \
  --mode accuracy \
  --workload "$PWD/examples/microservices/hotel-reservation/benchmark/workload.toml" \
  --seed random \
  --candidate-dir "$PWD/examples/microservices/hotel-reservation/hotelReservation" \
  --run-command-json '["docker","compose","up","-d","--build"]' \
  --stop-command-json '["docker","compose","stop","-t","10","frontend","geo","profile","rate","recommendation","reservation","search","user"]' \
  --cleanup-command-json '["docker","compose","down","-v","--remove-orphans"]' \
  --startup-timeout 120

go -C examples/evaluators/microservice run ./cmd/servicebench \
  --workload "$PWD/examples/microservices/hotel-reservation/benchmark/workload.toml" \
  --seed random \
  --fixture-seed random \
  --candidate-dir "$PWD/examples/microservices/hotel-reservation/hotelReservation" \
  --run-command-json '["docker","compose","up","-d","--build"]' \
  --stop-command-json '["docker","compose","stop","-t","10","frontend","geo","profile","rate","recommendation","reservation","search","user"]' \
  --cleanup-command-json '["docker","compose","down","-v","--remove-orphans"]' \
  --startup-timeout 120 \
  --output-json /tmp/hotel-benchmark.json
```

Reservations have no deletion API. Reset all persistent state between clean
comparisons:

```bash
docker compose \
  -f examples/microservices/hotel-reservation/hotelReservation/docker-compose.yml \
  down -v
```

## Verify telemetry capture

The optimization manifest wires OpenTelemetry capture into benchmark runs (see
`benchmark/otel/telemetry.toml` and the telemetry README under
`examples/evaluators/microservice/telemetry/`). To reproduce it against the
repository layout, replace the plain benchmark run command above with the
injected one and add the telemetry flags:

```bash
SCENARIO="$PWD/examples/microservices/hotel-reservation"
go -C examples/evaluators/microservice run ./cmd/servicebench trace \
  --workload "$SCENARIO/benchmark/workload.toml" \
  --seed random \
  --fixture-seed random \
  --candidate-dir "$SCENARIO/hotelReservation" \
  --run-command-json "[\"sh\",\"-c\",\"go -C $PWD/examples/evaluators/microservice run ./cmd/otelinject --compose $SCENARIO/hotelReservation/docker-compose.yml --config $SCENARIO/benchmark/otel/telemetry.toml --metrics-dir $SCENARIO/metrics/otel --output $SCENARIO/metrics/otel/compose.telemetry.yaml && docker compose -f docker-compose.yml -f $SCENARIO/metrics/otel/compose.telemetry.yaml up -d --build\"]" \
  --stop-command-json '["docker","compose","stop","-t","10","frontend","geo","profile","rate","recommendation","reservation","search","user"]' \
  --cleanup-command-json '["docker","compose","down","-v","--remove-orphans"]' \
  --startup-timeout 120 \
  --telemetry-command-json "[\"go\",\"run\",\"./cmd/otelcapture\",\"--input-json\",\"$SCENARIO/metrics/otel/traces.otlp.ndjson\",\"--settle-seconds\",\"5\"]" \
  --telemetry-output /tmp/hotel-telemetry.json \
  --trace-graph-json /tmp/hotel-trace-graph.json \
  --telemetry-timeout 60 \
  --output-json /tmp/hotel-benchmark.json
```

A successful run writes the normalized report to `/tmp/hotel-telemetry.json`
and the schema-v2 critical-path graph to `/tmp/hotel-trace-graph.json`, and
embeds normalized telemetry in the benchmark output. Remove
`examples/microservices/hotel-reservation/metrics/otel/traces.otlp.ndjson`
between runs to keep the raw capture small.

## Optimize

```bash
./vs --outer-loop agent \
  --input examples/microservices/hotel-reservation \
  --exp-name hotel-reservation-opt \
  --backend cpu --interface service \
  --profiler otel \
  --agent-backend cli --cli-provider codex \
  --max-rounds 10 --git-tracking --headless
```

The benchmark reports closed-loop logical operations per second. One logical
reservation includes both the capacity-filling request and the rejected
over-capacity read-back, so faster incorrect acknowledgements cannot improve the
trusted metric.
