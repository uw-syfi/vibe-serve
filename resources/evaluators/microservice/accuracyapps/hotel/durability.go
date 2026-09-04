package hotel

import (
	"context"
	"encoding/json"
	"fmt"
	"math/rand"
	"strconv"

	hotelsupport "vibesys/microservice-evaluator/appsupport/hotel"
)

// durableNight is one seeded night whose expected post-restart state the
// sequential model already knows.
type durableNight struct {
	FullHotel    string    `json:"full_hotel"`
	PartialHotel string    `json:"partial_hotel"`
	Night        [2]string `json:"night"`
}

// durableCounterexample reports state that an acknowledged reservation should
// have made durable but that did not survive the restart.
type durableCounterexample struct {
	SchemaVersion int            `json:"schema_version"`
	Property      string         `json:"property"`
	Reason        string         `json:"reason"`
	Nights        []durableNight `json:"nights"`
	Failing       durableNight   `json:"failing_night"`
	Expected      []string       `json:"expected_available_hotel_ids,omitempty"`
	Actual        []string       `json:"actual_available_hotel_ids,omitempty"`
}

func (c *durableCounterexample) Error() string {
	encoded, err := json.Marshal(c)
	if err != nil {
		return "Hotel durability mismatch: " + c.Reason
	}
	return "Hotel durability mismatch: " + string(encoded)
}

// verifyDurableState acknowledges reservations, crashes and restarts the
// candidate, then replays read-only observations against the same sequential
// model that generated the writes.
//
// The reservation endpoint is always checked. Search availability is checked
// only when the workload opts in: the pinned upstream implementation serves
// availability from a cache tier whose miss path never reaches the durable
// store, so a full hotel becomes bookable again as soon as that cache is lost.
func (a *Application) verifyDurableState(
	ctx context.Context,
	c client,
	seed int64,
	cases int,
	random *rand.Rand,
	restart func(context.Context) error,
	checkAvailability bool,
) (int, error) {
	username, password := hotelsupport.MustUser(0)
	model, err := newSequentialModel(a.catalog)
	if err != nil {
		return 0, err
	}
	rateIDs := sortedRateBackedIDs()
	random.Shuffle(len(rateIDs), func(left, right int) {
		rateIDs[left], rateIDs[right] = rateIDs[right], rateIDs[left]
	})
	if len(rateIDs) < 2 {
		return 0, fmt.Errorf("Hotel durability check needs at least two rate-backed hotels")
	}

	nights := make([]durableNight, 0, cases)
	checks := 0
	for caseIndex := 0; caseIndex < cases; caseIndex++ {
		if err := checkContext(ctx); err != nil {
			return checks, err
		}
		night := durableNight{
			FullHotel:    rateIDs[(caseIndex*2)%len(rateIDs)],
			PartialHotel: rateIDs[(caseIndex*2+1)%len(rateIDs)],
			Night:        namespacedNight(seed, durabilityOffset+caseIndex*nightsPerCase),
		}
		if night.FullHotel == night.PartialHotel {
			continue
		}
		for _, item := range []struct {
			hotelID string
			slack   int
			label   string
		}{
			{night.FullHotel, 0, "durable-full"},
			{night.PartialHotel, 1, "durable-partial"},
		} {
			capacity, err := capacityForHotel(item.hotelID)
			if err != nil {
				return checks, err
			}
			query := reservationActionQuery(
				item.hotelID, night.Night, capacity-item.slack,
				fmt.Sprintf("%s-%d", item.label, caseIndex), username, password,
			)
			checks++
			if err := c.exactMessage(ctx, "/reservation", query, reservationSuccess); err != nil {
				return checks, fmt.Errorf("durability case %d setup for hotel %s: %w", caseIndex, item.hotelID, err)
			}
			if _, err := model.apply(differentialAction{Kind: actionReserve, Query: query}); err != nil {
				return checks, err
			}
		}
		nights = append(nights, night)
	}
	if len(nights) == 0 {
		return checks, fmt.Errorf("Hotel durability check generated no nights")
	}

	if err := restart(ctx); err != nil {
		return checks, fmt.Errorf("restart candidate for durability replay: %w", err)
	}

	for _, night := range nights {
		if err := checkContext(ctx); err != nil {
			return checks, err
		}
		// Search membership is read-only, so it must run before the capacity
		// probes consume the partial hotel's last room.
		if checkAvailability {
			expected, err := model.apply(differentialAction{
				Kind: actionSearch, Query: searchQuery(night.Night, a.catalog[night.FullHotel], true),
			})
			if err != nil {
				return checks, err
			}
			features, err := c.geoJSON(ctx, "/hotels", searchQuery(night.Night, a.catalog[night.FullHotel], true))
			checks++
			if err != nil {
				return checks, err
			}
			if err := validateProfiles(features, a.catalog, "post-restart durability search"); err != nil {
				return checks, err
			}
			actual := make(map[string]struct{}, len(features))
			for id := range features {
				actual[id] = struct{}{}
			}
			if err := exactIDs(features, expected.HotelIDs...); err != nil {
				return checks, &durableCounterexample{
					SchemaVersion: durableSchemaVersion,
					Property:      "durable_availability",
					Reason: fmt.Sprintf(
						"search availability did not survive the restart: %v", err,
					),
					Nights:   nights,
					Failing:  night,
					Expected: expected.HotelIDs,
					Actual:   sortedHotelIDs(actual),
				}
			}
		}
		for _, hotelID := range []string{night.FullHotel, night.PartialHotel} {
			capacity, err := capacityForHotel(hotelID)
			if err != nil {
				return checks, err
			}
			reserved := capacity
			if hotelID == night.PartialHotel {
				reserved = capacity - 1
			}
			probes, err := a.probeRemaining(
				ctx, c, hotelID, night.Night, reserved, "durable-probe-"+strconv.Itoa(reserved),
			)
			checks += probes
			if err != nil {
				return checks, &durableCounterexample{
					SchemaVersion: durableSchemaVersion,
					Property:      "durable_capacity",
					Reason:        fmt.Sprintf("acknowledged reservation did not survive the restart: %v", err),
					Nights:        nights,
					Failing:       night,
				}
			}
		}
	}
	return checks, nil
}
