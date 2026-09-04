# Hotel accuracy oracle

The Hotel accuracy adapter owns an independent compatibility oracle for the
pinned DeathStarBench Hotel Reservation API. Existing targeted checks validate
strict response schemas, seed catalogs, negative requests, state transitions,
and managed crash recovery.

The sequential differential check adds a deliberately simple in-memory model.
A hidden seed generates a replayable history spanning login, search,
recommendation, and reservation requests. Each action is applied to the model
and sent to the configured service target through the shared evaluator runtime.
The first mismatch fails accuracy with a versioned JSON history prefix,
expected observation, and actual observation or protocol error.

The model preserves externally observable reference behavior, including
capacity by hotel and night, atomic over-capacity rejection, search visibility,
hotel/date isolation, and the pinned frontend behavior where an invalid-login
reservation may mutate capacity before returning the authentication failure.
GeoJSON object and feature order are normalized, while schemas, profile values,
messages, and result membership remain exact.

This package does not model internal gRPC calls, databases, caches, timing, or
service topology.

## Beyond one sequential history

Four checks extend the model past a single serial request stream. Each owns a
disjoint slice of the seeded date namespace (see `namespaces.go`), because the
public API has no delete operation.

| Property | What it asserts |
| --- | --- |
| `degenerate_date_ranges` | A range enclosing no nights (`inDate == outDate`, or reversed) is acknowledged and consumes nothing. |
| `multi_night_atomicity` | A span over a full interior night is rejected whole, and the flanking nights keep their rooms. |
| `concurrent_isolation` | Simultaneous single-room reservations on distinct hotels all succeed and each consumes exactly one room. |
| `durable_capacity` | Acknowledged reservations replay correctly against the model after a managed restart. |

Concurrency is compared against the set of linearizable outcomes rather than one
expected response. Contention is shaped so that set is a singleton: a night is
filled to `k` remaining rooms, then more than `k` simultaneous single-room
requests are issued, and every serialization acknowledges exactly `k`. Capacity
is read back through a two-sided probe: an over-capacity request is rejected
and therefore leaves state untouched, and an exact-capacity request then
succeeds, pinning the consumed rooms from both directions.

## Opt-in properties the pinned reference violates

Three properties are declared but only checked when a workload asks for them,
because unmodified DeathStarBench fails all three. Turning one on rejects the
reference implementation, not just a regressed candidate.

```toml
[application_config]
strict_linearizable_capacity = true
strict_durable_availability = true
strict_endpoint_liveness = true
```

| Property | Upstream behavior |
| --- | --- |
| `linearizable_capacity` | `MakeReservation` reads the room count from memcached, releases it, and writes back after the capacity test, so simultaneous requests oversell. |
| `durable_availability` | `CheckAvailability` serves availability from cached counts; its datastore miss path is unreachable, so a full hotel is advertised again once the cache tier restarts. |
| `endpoint_liveness` | The capacity lookup calls `log.Panic` when no seeded row matches, so one request for an out-of-catalog hotel takes the reservation service down. |

`DIFFERENTIAL-FINDINGS.md` records the measured counterexamples, the exact
commands, and what they imply for using this oracle as an accuracy gate.

## Scope

The sequential differential check does not claim concurrency or crash/recovery
equivalence on its own; the checks above cover those dimensions with allowed
outcome sets and lifecycle actions. Interleaved read/write coherence during a
burst, in-flight requests interrupted by a crash, and multi-key transactional
histories remain unmodeled.
