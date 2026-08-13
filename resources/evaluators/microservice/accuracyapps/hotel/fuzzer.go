package hotel

import (
	"context"
	"encoding/json"
	"fmt"
	"math/rand"
	"reflect"
	"sort"
	"strconv"
	"time"

	hotelsupport "vibesys/microservice-evaluator/appsupport/hotel"
)

type differentialActionKind string

const (
	actionLogin     differentialActionKind = "login"
	actionRecommend differentialActionKind = "recommend"
	actionReserve   differentialActionKind = "reserve"
	actionSearch    differentialActionKind = "search"
)

type differentialAction struct {
	Kind  differentialActionKind `json:"kind"`
	Query map[string]string      `json:"query"`
}

type differentialObservation struct {
	Message  string   `json:"message,omitempty"`
	HotelIDs []string `json:"hotel_ids,omitempty"`
}

type differentialCounterexample struct {
	SchemaVersion int                      `json:"schema_version"`
	Step          int                      `json:"step"`
	History       []differentialAction     `json:"history"`
	Expected      differentialObservation  `json:"expected"`
	Actual        *differentialObservation `json:"actual,omitempty"`
	ActualError   string                   `json:"actual_error,omitempty"`
}

func (c *differentialCounterexample) Error() string {
	encoded, err := json.Marshal(c)
	if err != nil {
		return fmt.Sprintf("Hotel sequential differential mismatch at step %d", c.Step)
	}
	return "Hotel sequential differential mismatch: " + string(encoded)
}

func (a *Application) verifySequentialDifferential(
	ctx context.Context,
	c client,
	seed int64,
	cases int,
) (int, error) {
	history, err := generateSequentialHistory(seed, cases, a.catalog)
	if err != nil {
		return 0, err
	}
	model, err := newSequentialModel(a.catalog)
	if err != nil {
		return 0, err
	}
	for index, action := range history {
		if err := checkContext(ctx); err != nil {
			return index, err
		}
		expected, err := model.apply(action)
		if err != nil {
			return index, fmt.Errorf("apply Hotel model action %d: %w", index, err)
		}
		actual, actualErr := a.observeDifferentialAction(ctx, c, action)
		if actualErr != nil || !reflect.DeepEqual(actual, expected) {
			counterexample := &differentialCounterexample{
				SchemaVersion: 1,
				Step:          index,
				History:       append([]differentialAction(nil), history[:index+1]...),
				Expected:      expected,
			}
			if actualErr != nil {
				counterexample.ActualError = actualErr.Error()
			} else {
				counterexample.Actual = &actual
			}
			return index + 1, counterexample
		}
	}
	return len(history), nil
}

func (a *Application) observeDifferentialAction(
	ctx context.Context,
	c client,
	action differentialAction,
) (differentialObservation, error) {
	switch action.Kind {
	case actionLogin:
		message, err := c.message(ctx, "/user", action.Query)
		if err != nil {
			return differentialObservation{}, err
		}
		return messageObservation(message), nil
	case actionReserve:
		message, err := c.message(ctx, "/reservation", action.Query)
		if err != nil {
			return differentialObservation{}, err
		}
		return messageObservation(message), nil
	case actionRecommend, actionSearch:
		path := "/recommendations"
		if action.Kind == actionSearch {
			path = "/hotels"
		}
		features, err := c.geoJSON(ctx, path, action.Query)
		if err != nil {
			return differentialObservation{}, err
		}
		if err := validateProfiles(features, a.catalog, "sequential differential "+string(action.Kind)); err != nil {
			return differentialObservation{}, err
		}
		ids := make([]string, 0, len(features))
		for id := range features {
			ids = append(ids, id)
		}
		sortHotelIDs(ids)
		return hotelIDsObservation(ids...), nil
	default:
		return differentialObservation{}, fmt.Errorf("unknown Hotel differential action %q", action.Kind)
	}
}

func generateSequentialHistory(
	seed int64,
	cases int,
	catalog map[string]profile,
) ([]differentialAction, error) {
	if cases < 1 {
		return nil, fmt.Errorf("Hotel differential cases must be positive, got %d", cases)
	}
	if len(catalog) != 80 {
		return nil, fmt.Errorf("Hotel differential catalog has %d profiles, expected 80", len(catalog))
	}
	random := rand.New(rand.NewSource(seed ^ 0x4d6f64656c))
	history := make([]differentialAction, 0, 5+cases*9)

	userIndex := random.Intn(501)
	username, password, err := hotelsupport.User(userIndex)
	if err != nil {
		return nil, err
	}
	history = append(history,
		differentialAction{Kind: actionLogin, Query: credentials(username, password)},
		differentialAction{Kind: actionLogin, Query: credentials(username, password+"-wrong")},
		differentialAction{Kind: actionRecommend, Query: recommendationQuery("price", catalog["2"], false)},
		differentialAction{Kind: actionRecommend, Query: recommendationQuery("rate", catalog["9"], true)},
	)
	distanceID := strconv.Itoa(1 + random.Intn(80))
	history = append(history, differentialAction{
		Kind:  actionRecommend,
		Query: recommendationQuery("dis", catalog[distanceID], random.Intn(2) == 0),
	})

	rateIDs := sortedRateBackedIDs()
	random.Shuffle(len(rateIDs), func(left, right int) {
		rateIDs[left], rateIDs[right] = rateIDs[right], rateIDs[left]
	})
	start := reservationDate(seed).AddDate(0, 0, 1100)
	for caseIndex := 0; caseIndex < cases; caseIndex++ {
		primary := rateIDs[caseIndex%len(rateIDs)]
		isolation := rateIDs[(caseIndex+1)%len(rateIDs)]
		caseStart := start.AddDate(0, 0, caseIndex*4)
		primaryDates := [2]string{
			caseStart.Format(time.DateOnly),
			caseStart.AddDate(0, 0, 1).Format(time.DateOnly),
		}
		authDates := [2]string{
			caseStart.AddDate(0, 0, 2).Format(time.DateOnly),
			caseStart.AddDate(0, 0, 3).Format(time.DateOnly),
		}
		capacity, err := capacityForHotel(primary)
		if err != nil {
			return nil, err
		}
		firstRooms := 1 + random.Intn(capacity-1)
		secondRooms := capacity - firstRooms
		validUser, validPassword, err := hotelsupport.User(random.Intn(501))
		if err != nil {
			return nil, err
		}

		history = append(history,
			differentialAction{Kind: actionSearch, Query: searchQuery(primaryDates, catalog[primary], caseIndex%2 == 0)},
			differentialAction{Kind: actionReserve, Query: reservationActionQuery(
				primary, primaryDates, firstRooms, fmt.Sprintf("differential-%d-first", caseIndex), validUser, validPassword,
			)},
			differentialAction{Kind: actionReserve, Query: reservationActionQuery(
				primary, primaryDates, secondRooms, fmt.Sprintf("differential-%d-fill", caseIndex), validUser, validPassword,
			)},
			differentialAction{Kind: actionSearch, Query: searchQuery(primaryDates, catalog[primary], caseIndex%2 != 0)},
			differentialAction{Kind: actionReserve, Query: reservationActionQuery(
				primary, primaryDates, 1, fmt.Sprintf("differential-%d-over", caseIndex), validUser, validPassword,
			)},
			differentialAction{Kind: actionReserve, Query: reservationActionQuery(
				primary, authDates, capacity, fmt.Sprintf("differential-%d-invalid-auth", caseIndex), validUser, validPassword+"-wrong",
			)},
			differentialAction{Kind: actionReserve, Query: reservationActionQuery(
				primary, authDates, 1, fmt.Sprintf("differential-%d-auth-readback", caseIndex), validUser, validPassword,
			)},
			differentialAction{Kind: actionSearch, Query: searchQuery(authDates, catalog[primary], true)},
			differentialAction{Kind: actionReserve, Query: reservationActionQuery(
				isolation, authDates, 1, fmt.Sprintf("differential-%d-isolation", caseIndex), validUser, validPassword,
			)},
		)
	}
	return history, nil
}

func recommendationQuery(require string, item profile, locale bool) map[string]string {
	query := map[string]string{
		"require": require,
		"lat":     strconv.FormatFloat(item.recommendLat, 'f', -1, 64),
		"lon":     strconv.FormatFloat(item.recommendLon, 'f', -1, 64),
	}
	if locale {
		query["locale"] = "en"
	}
	return query
}

func searchQuery(dates [2]string, item profile, locale bool) map[string]string {
	query := map[string]string{
		"inDate": dates[0], "outDate": dates[1],
		"lat": strconv.FormatFloat(item.recommendLat, 'f', -1, 64),
		"lon": strconv.FormatFloat(item.recommendLon, 'f', -1, 64),
	}
	if locale {
		query["locale"] = "en"
	}
	return query
}

func reservationActionQuery(
	hotelID string,
	dates [2]string,
	rooms int,
	customerName string,
	username string,
	password string,
) map[string]string {
	query := credentials(username, password)
	query["hotelId"] = hotelID
	query["inDate"] = dates[0]
	query["outDate"] = dates[1]
	query["number"] = strconv.Itoa(rooms)
	query["customerName"] = customerName
	return query
}

func messageObservation(message string) differentialObservation {
	return differentialObservation{Message: message}
}

func hotelIDsObservation(ids ...string) differentialObservation {
	result := append([]string(nil), ids...)
	sortHotelIDs(result)
	return differentialObservation{HotelIDs: result}
}

func sortHotelIDs(ids []string) {
	sort.Slice(ids, func(left, right int) bool {
		leftID, leftErr := strconv.Atoi(ids[left])
		rightID, rightErr := strconv.Atoi(ids[right])
		if leftErr != nil || rightErr != nil {
			return ids[left] < ids[right]
		}
		return leftID < rightID
	})
}
