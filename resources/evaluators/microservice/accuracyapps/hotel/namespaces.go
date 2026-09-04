package hotel

// Reservation fixtures are permanent: the public API has no delete operation.
// Every check therefore owns a disjoint day range inside the hidden, seeded
// date namespace. Offsets are spaced far enough apart that raising the case
// count cannot make two checks share a night.
const (
	nightsPerCase = 4

	concurrentIsolationOffset = 2000
	concurrentCapacityOffset  = 2100
	durabilityOffset          = 2400
	degenerateOffset          = 2700
	multiNightOffset          = 2800
	livenessOffset            = 2900
)

const (
	concurrentSchemaVersion = 1
	durableSchemaVersion    = 1
)
