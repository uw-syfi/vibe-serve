package hotel

import (
	"context"
	"encoding/json"
	"fmt"
	"math/rand"
	"sort"
	"strconv"
	"sync"
	"time"

	hotelsupport "vibesys/microservice-evaluator/appsupport/hotel"
)

// concurrentOutcome is one response from a burst of simultaneous requests.
// Index identifies the request inside the burst so a counterexample replays.
type concurrentOutcome struct {
	Index   int    `json:"index"`
	Message string `json:"message,omitempty"`
	Error   string `json:"error,omitempty"`
}

// concurrentCounterexample reports a burst whose responses admit no
// serialization of the same requests against the sequential model.
type concurrentCounterexample struct {
	SchemaVersion int                  `json:"schema_version"`
	Property      string               `json:"property"`
	Reason        string               `json:"reason"`
	HotelID       string               `json:"hotel_id,omitempty"`
	HotelIDs      []string             `json:"hotel_ids,omitempty"`
	Night         [2]string            `json:"night"`
	Capacity      int                  `json:"capacity,omitempty"`
	Reserved      int                  `json:"reserved_before_burst"`
	Remaining     int                  `json:"rooms_remaining_before_burst"`
	Concurrency   int                  `json:"concurrency"`
	RoomsPer      int                  `json:"rooms_per_request"`
	Accepted      int                  `json:"accepted"`
	Rejected      int                  `json:"rejected"`
	Outcomes      []concurrentOutcome  `json:"outcomes"`
	Setup         []differentialAction `json:"setup"`
}

func (c *concurrentCounterexample) Error() string {
	encoded, err := json.Marshal(c)
	if err != nil {
		return "Hotel concurrent differential mismatch: " + c.Reason
	}
	return "Hotel concurrent differential mismatch: " + string(encoded)
}

// reserveConcurrently issues every reservation at once and returns responses in
// request order. The shared runtime owns one HTTP client per target and is safe
// for concurrent use, so the burst measures the candidate rather than the
// harness.
func (a *Application) reserveConcurrently(
	ctx context.Context,
	c client,
	queries []map[string]string,
) []concurrentOutcome {
	outcomes := make([]concurrentOutcome, len(queries))
	release := make(chan struct{})
	var group sync.WaitGroup
	for index, query := range queries {
		group.Add(1)
		go func(index int, query map[string]string) {
			defer group.Done()
			<-release
			message, err := c.message(ctx, "/reservation", query)
			if err != nil {
				outcomes[index] = concurrentOutcome{Index: index, Error: err.Error()}
				return
			}
			outcomes[index] = concurrentOutcome{Index: index, Message: message}
		}(index, query)
	}
	close(release)
	group.Wait()
	return outcomes
}

// tallyBurst classifies burst responses. Any transport failure or unexpected
// message is fatal: a reservation whose outcome is unknown cannot be placed in
// a serialization, so the check refuses to guess.
func tallyBurst(outcomes []concurrentOutcome) (accepted int, rejected int, reason string) {
	for _, outcome := range outcomes {
		switch {
		case outcome.Error != "":
			return accepted, rejected, fmt.Sprintf(
				"request %d failed in transport, so its effect is unknown: %s", outcome.Index, outcome.Error,
			)
		case outcome.Message == reservationSuccess:
			accepted++
		case outcome.Message == reservationFailure:
			rejected++
		default:
			return accepted, rejected, fmt.Sprintf(
				"request %d returned message %q, which is neither acknowledgement nor rejection",
				outcome.Index, outcome.Message,
			)
		}
	}
	return accepted, rejected, ""
}

// verifyConcurrentIsolation issues one simultaneous single-room reservation per
// hotel on a shared night. Distinct hotels never contend, so every linearization
// accepts every request and leaves exactly one room consumed per hotel.
func (a *Application) verifyConcurrentIsolation(
	ctx context.Context,
	c client,
	seed int64,
	random *rand.Rand,
) (int, error) {
	username, password := hotelsupport.MustUser(0)
	rateIDs := sortedRateBackedIDs()
	random.Shuffle(len(rateIDs), func(left, right int) {
		rateIDs[left], rateIDs[right] = rateIDs[right], rateIDs[left]
	})
	hotels := rateIDs[:min(6, len(rateIDs))]
	sortHotelIDs(hotels)
	night := namespacedNight(seed, concurrentIsolationOffset)

	queries := make([]map[string]string, 0, len(hotels))
	setup := make([]differentialAction, 0, len(hotels))
	for index, hotelID := range hotels {
		query := reservationActionQuery(
			hotelID, night, 1, fmt.Sprintf("concurrent-isolation-%d", index), username, password,
		)
		queries = append(queries, query)
		setup = append(setup, differentialAction{Kind: actionReserve, Query: query})
	}
	outcomes := a.reserveConcurrently(ctx, c, queries)
	checks := len(queries)
	accepted, rejected, reason := tallyBurst(outcomes)
	if reason == "" && accepted != len(queries) {
		reason = fmt.Sprintf(
			"%d of %d non-contending reservations were rejected; distinct hotels share no capacity",
			rejected, len(queries),
		)
	}
	if reason != "" {
		return checks, &concurrentCounterexample{
			SchemaVersion: concurrentSchemaVersion,
			Property:      "concurrent_isolation",
			Reason:        reason,
			HotelIDs:      hotels,
			Night:         night,
			Concurrency:   len(queries),
			RoomsPer:      1,
			Accepted:      accepted,
			Rejected:      rejected,
			Outcomes:      outcomes,
			Setup:         setup,
		}
	}

	// Each hotel must have consumed exactly one room, not zero and not one per
	// concurrent peer. Both bounds are read back through the public endpoint.
	for _, hotelID := range hotels {
		probes, err := a.probeRemaining(ctx, c, hotelID, night, 1, "concurrent-isolation-probe")
		checks += probes
		if err != nil {
			return checks, fmt.Errorf("concurrent isolation readback for hotel %s: %w", hotelID, err)
		}
	}
	return checks, nil
}

// verifyLinearizableCapacity fills a night to a small remainder, then issues
// more simultaneous single-room reservations than there are rooms left. Every
// serialization of that burst accepts exactly the remaining rooms, so any other
// acknowledgement count is a linearizability violation rather than a tolerable
// interleaving.
func (a *Application) verifyLinearizableCapacity(
	ctx context.Context,
	c client,
	seed int64,
	cases int,
	random *rand.Rand,
) (int, error) {
	username, password := hotelsupport.MustUser(0)
	rateIDs := sortedRateBackedIDs()
	checks := 0
	for caseIndex := 0; caseIndex < cases; caseIndex++ {
		if err := checkContext(ctx); err != nil {
			return checks, err
		}
		hotelID := rateIDs[random.Intn(len(rateIDs))]
		capacity, err := capacityForHotel(hotelID)
		if err != nil {
			return checks, err
		}
		night := namespacedNight(seed, concurrentCapacityOffset+caseIndex*nightsPerCase)
		remaining := 1 + random.Intn(3)
		concurrency := remaining + 8 + random.Intn(16)

		fill := reservationActionQuery(
			hotelID, night, capacity-remaining,
			fmt.Sprintf("linearizable-%d-fill", caseIndex), username, password,
		)
		checks++
		if err := c.exactMessage(ctx, "/reservation", fill, reservationSuccess); err != nil {
			return checks, fmt.Errorf("linearizable capacity case %d setup: %w", caseIndex, err)
		}

		queries := make([]map[string]string, 0, concurrency)
		for index := 0; index < concurrency; index++ {
			queries = append(queries, reservationActionQuery(
				hotelID, night, 1,
				fmt.Sprintf("linearizable-%d-race-%d", caseIndex, index), username, password,
			))
		}
		outcomes := a.reserveConcurrently(ctx, c, queries)
		checks += concurrency
		accepted, rejected, reason := tallyBurst(outcomes)
		if reason == "" && accepted != remaining {
			reason = fmt.Sprintf(
				"%d simultaneous single-room reservations were acknowledged against %d remaining rooms; "+
					"every serialization of this burst acknowledges exactly %d",
				accepted, remaining, remaining,
			)
		}
		if reason != "" {
			return checks, &concurrentCounterexample{
				SchemaVersion: concurrentSchemaVersion,
				Property:      "linearizable_capacity",
				Reason:        reason,
				HotelID:       hotelID,
				Night:         night,
				Capacity:      capacity,
				Reserved:      capacity - remaining,
				Remaining:     remaining,
				Concurrency:   concurrency,
				RoomsPer:      1,
				Accepted:      accepted,
				Rejected:      rejected,
				Outcomes:      outcomes,
				Setup:         []differentialAction{{Kind: actionReserve, Query: fill}},
			}
		}
		probes, err := a.probeRemaining(ctx, c, hotelID, night, capacity, "linearizable-"+strconv.Itoa(caseIndex)+"-probe")
		checks += probes
		if err != nil {
			return checks, fmt.Errorf("linearizable capacity case %d readback: %w", caseIndex, err)
		}
	}
	return checks, nil
}

// probeRemaining pins the consumed rooms on one night from both sides. The
// over-capacity request is rejected and therefore leaves state untouched; the
// exact-capacity request then fills the night, so callers must use a night they
// do not read again.
func (a *Application) probeRemaining(
	ctx context.Context,
	c client,
	hotelID string,
	night [2]string,
	reserved int,
	label string,
) (int, error) {
	username, password := hotelsupport.MustUser(0)
	capacity, err := capacityForHotel(hotelID)
	if err != nil {
		return 0, err
	}
	remaining := capacity - reserved
	over := reservationActionQuery(hotelID, night, remaining+1, label+"-over", username, password)
	if err := c.exactMessage(ctx, "/reservation", over, reservationFailure); err != nil {
		return 1, fmt.Errorf("hotel %s night %s consumed fewer than %d rooms: %w", hotelID, night[0], reserved, err)
	}
	if remaining == 0 {
		return 1, nil
	}
	exact := reservationActionQuery(hotelID, night, remaining, label+"-exact", username, password)
	if err := c.exactMessage(ctx, "/reservation", exact, reservationSuccess); err != nil {
		return 2, fmt.Errorf("hotel %s night %s consumed more than %d rooms: %w", hotelID, night[0], reserved, err)
	}
	return 2, nil
}

// namespacedNight derives one disjoint single night from the hidden seed. The
// endpoint has no delete operation, so every check owns its own date range.
func namespacedNight(seed int64, dayOffset int) [2]string {
	start := reservationDate(seed).AddDate(0, 0, dayOffset)
	return [2]string{start.Format(time.DateOnly), start.AddDate(0, 0, 1).Format(time.DateOnly)}
}

func sortedHotelIDs(ids map[string]struct{}) []string {
	result := make([]string, 0, len(ids))
	for id := range ids {
		result = append(result, id)
	}
	sort.Strings(result)
	sortHotelIDs(result)
	return result
}
