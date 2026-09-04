# Differential fuzzing findings: Hotel Reservation

Working notes for [issue #254](https://github.com/uw-syfi/vibesys/issues/254).
Question under test: can a simple reference model plus fuzzed request histories
act as a correctness oracle for the accuracy gate, and can that oracle actually
reject?

Answer so far: yes, it rejects, and the first things it rejects are defects in
the unmodified reference rather than in an optimized candidate. Calibration is
therefore not a preliminary step that converges and then goes away. It is a
standing decision about which upstream defects the gate is willing to inherit.

## Setup

- Target: `examples/microservices/repositories/deathstarbench/hotelReservation`
  at the pinned commit, `docker compose up -d`, gateway on `http://localhost:5000`.
- Oracle: `resources/evaluators/microservice/accuracyapps/hotel`.
- Runner: `go run ./cmd/servicebench --mode accuracy` from
  `resources/evaluators/microservice`.
- Reservation fixtures are permanent. Every run needs a fresh `--seed`; reusing
  one replays into its own residue and fails on stale capacity.

## Finding 1: concurrent reservations oversell

`services/reservation/server.go` reads the per-night room count from memcached,
drops it, tests capacity, and only then writes the incremented value back. There
is no CAS and no lock. Simultaneous requests all observe the pre-burst count.

Direct measurement against the running stack: fill a night to one remaining
room, then issue 32 simultaneous single-room requests.

| Trial | Rooms remaining | Concurrency | Acknowledged |
| --- | --- | --- | --- |
| 1 | 1 | 32 | 2 |
| 2 | 1 | 32 | 5 |
| 3 | 1 | 32 | 4 |
| 4 | 1 | 32 | 3 |
| 5 | 1 | 32 | 5 |

The overselling is durable, not just a response-level artifact. After one trial,
`reservation-db` held 204 rooms of a 200-room hotel for that night:

```
db.reservation.aggregate([
  {$match: {hotelId: "9", inDate: "7009-01-01"}},
  {$group: {_id: null, total: {$sum: "$number"}, rows: {$sum: 1}}}
])
// [{_id: null, total: 204, rows: 6}]
```

Through the gate, with `strict_linearizable_capacity = true`:

```
"valid": false,
"error": "Hotel concurrent differential mismatch: {\"property\":\"linearizable_capacity\",
  \"reason\":\"3 simultaneous single-room reservations were acknowledged against 1 remaining
  rooms; every serialization of this burst acknowledges exactly 1\",\"hotel_id\":\"33\",
  \"night\":[\"7248-04-03\",\"7248-04-04\"],\"capacity\":200,\"reserved_before_burst\":199,
  \"concurrency\":11,\"accepted\":3,\"rejected\":8, ...}"
```

This is a genuine linearizability violation, not tolerable nondeterminism. With
`k` rooms left and only single-room requests in flight, every serialization
acknowledges exactly `k`, so the allowed outcome set is a singleton and the
oracle needs no interleaving search to decide.

## Finding 2: search availability does not survive cache loss

`CheckAvailability` gathers per-night counts with `GetMulti`. gomemcache returns
a nil error for partial misses, so the `err == memcache.ErrCacheMiss` branch that
would query MongoDB is unreachable. Availability is decided entirely by whatever
is currently cached, and an uncached hotel defaults to available.

Fill hotel 9 for a night, confirm it disappears from `/hotels`, restart
`memcached-reserve`, and it returns:

```
before restart: 66 72 69 57 42 45 78 63 18 39 15 24 30 36 60 21 27 51 75 33 12 48 54 3 2 1
after restart:  ... 9 ...          # advertised as available
GET /reservation ... number=1   -> {"message":"Failed. Already reserved. "}
```

`/hotels` and `/reservation` disagree about the same night. Through the gate,
with the cache tier included in the stop command, the pre-existing
`crash_recovery` check already rejects:

```
"valid": false,
"error": "filled hotel 9 reappeared in search after restart"
```

This is why the current task definition stops only the stateless tier. With that
stop command the reference passes (`crash_recovery: true`, `durable_capacity:
true`, 343 checks), which means the harness's default restart scope is doing
real work in hiding a durability defect, not merely keeping the run cheap.

## Finding 3: an out-of-catalog hotel takes the service down

The capacity lookup calls `log.Panic` when `numCollection.FindOne` matches
nothing. A single request for an unknown `hotelId` kills the reservation
service; only the compose `restart: always` policy brings it back, roughly 20
seconds later. During that window every reservation returns HTTP 500.

With `strict_endpoint_liveness = true`:

```
"valid": false,
"error": "reservation endpoint stopped serving after a request for out-of-catalog
  hotel 1563991: GET /reservation: HTTP 500, expected 200; body=\"rpc error:
  code = Unavailable ... connect: connection refused\""
```

The existing negative-case check missed this because it only removes required
parameters, never supplies a well-formed value with no matching row.

## Behavior the model now pins deliberately

Calibration also turned up upstream behavior that is odd but consistent, so the
model reproduces it rather than rejecting it:

- A range enclosing no nights (`inDate == outDate`, or reversed) is
  acknowledged and consumes nothing. The frontend validates only the
  `YYYY-MM-DD` shape; the night loop then runs zero times.
- `number` is optional and parsed with the error discarded, so a non-numeric
  value books zero rooms and succeeds.
- A negative `number` is accepted and decrements the count, freeing capacity.
  Confirmed on a full night: `-50` was acknowledged, and a subsequent `+50` was
  acknowledged too.
- A reservation with invalid credentials still consumes capacity before the
  authentication failure is returned, and the over-capacity message wins when
  both conditions hold.

Multi-night reservations are genuinely atomic upstream: a span over a full
interior night is rejected whole and leaves the flanking nights untouched.

## What this says about the method

- The oracle can reject, and its counterexamples are minimal and replayable: a
  JSON prefix of the history, the burst outcomes in request order, and the
  seeded night, with the run seed withheld.
- Every rejection so far came from the concurrency and persistence dimensions.
  A sequential steady-state checker finds none of them, which supports treating
  both as mandatory dimensions rather than extras.
- The false-positive rate is not the interesting number. All three findings are
  real defects; the question is whether the gate should fail a candidate for
  behavior the reference also exhibits. Making them opt-in keeps the default
  gate calibrated while letting an experiment demand the stronger contract.
- Open risk for the optimization phase: an optimizer that adds caching or
  batching will most likely trip `linearizable_capacity` and
  `durable_availability` in ways that are indistinguishable from the upstream
  defects. Attribution needs the baseline result for the same seed alongside the
  candidate result.

## Not yet covered

- Read/write coherence observed during a burst (only the post-burst state is
  checked).
- Crashes injected mid-request, so an in-flight reservation's outcome is
  ambiguous. This needs allowed outcome sets over the acknowledgement itself.
- Concurrent bursts mixing room counts, or spanning multiple nights, where the
  allowed outcome set stops being a singleton and needs a real linearizability
  search.
