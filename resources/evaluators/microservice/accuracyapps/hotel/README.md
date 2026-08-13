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
service topology. The sequential differential check also does not claim
concurrency or crash/recovery equivalence; those require histories with allowed
outcome sets and lifecycle actions. The existing targeted crash-recovery check
remains an independent optional property when managed lifecycle support is
available.
