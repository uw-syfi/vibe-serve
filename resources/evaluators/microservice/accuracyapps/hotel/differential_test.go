package hotel

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"

	"vibesys/microservice-evaluator/api"
	hotelsupport "vibesys/microservice-evaluator/appsupport/hotel"
)

func TestSequentialHistoryIsDeterministicVariedAndStateful(t *testing.T) {
	catalog, err := seedProfiles()
	if err != nil {
		t.Fatal(err)
	}
	first, err := generateSequentialHistory(41, 4, catalog)
	if err != nil {
		t.Fatal(err)
	}
	replay, err := generateSequentialHistory(41, 4, catalog)
	if err != nil {
		t.Fatal(err)
	}
	other, err := generateSequentialHistory(42, 4, catalog)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(first, replay) {
		t.Fatal("same seed did not reproduce the exact Hotel history")
	}
	if reflect.DeepEqual(first, other) {
		t.Fatal("different seeds generated identical Hotel histories")
	}
	if len(first) != 5+4*9 {
		t.Fatalf("history length=%d, want %d", len(first), 5+4*9)
	}

	caseActions := first[5 : 5+9]
	for index, want := range []differentialActionKind{
		actionSearch,
		actionReserve,
		actionReserve,
		actionSearch,
		actionReserve,
		actionReserve,
		actionReserve,
		actionSearch,
		actionReserve,
	} {
		if caseActions[index].Kind != want {
			t.Fatalf("case action %d=%q, want %q", index, caseActions[index].Kind, want)
		}
	}
	if caseActions[1].Query["hotelId"] != caseActions[5].Query["hotelId"] ||
		caseActions[1].Query["inDate"] == caseActions[5].Query["inDate"] ||
		caseActions[5].Query["inDate"] != caseActions[7].Query["inDate"] {
		t.Fatal("history does not connect same-hotel writes to exact-date and disjoint-date reads")
	}
	if caseActions[8].Query["hotelId"] == caseActions[5].Query["hotelId"] {
		t.Fatal("history does not include same-date hotel isolation")
	}
}

func TestSequentialModelPreservesPinnedReservationBehavior(t *testing.T) {
	catalog, _ := seedProfiles()
	model, err := newSequentialModel(catalog)
	if err != nil {
		t.Fatal(err)
	}
	username, password := hotelsupport.MustUser(8)
	dates := [2]string{"6100-04-12", "6100-04-13"}

	invalidAuth := differentialAction{
		Kind: actionReserve,
		Query: reservationActionQuery(
			"9", dates, 200, "invalid-auth", username, password+"-wrong",
		),
	}
	got, err := model.apply(invalidAuth)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, messageObservation(loginFailure)) {
		t.Fatalf("invalid-auth observation=%+v", got)
	}

	readback := invalidAuth
	readback.Query = reservationActionQuery("9", dates, 1, "readback", username, password)
	got, err = model.apply(readback)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, messageObservation(reservationFailure)) {
		t.Fatalf("invalid-auth reservation did not consume capacity: %+v", got)
	}

	search, err := model.apply(differentialAction{
		Kind:  actionSearch,
		Query: searchQuery(dates, catalog["9"], true),
	})
	if err != nil {
		t.Fatal(err)
	}
	if containsHotelID(search.HotelIDs, "9") {
		t.Fatalf("filled hotel 9 remained searchable: %+v", search)
	}

	atomicDates := [2]string{"6100-04-14", "6100-04-15"}
	overCapacity := differentialAction{
		Kind:  actionReserve,
		Query: reservationActionQuery("9", atomicDates, 201, "over", username, password),
	}
	got, err = model.apply(overCapacity)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, messageObservation(reservationFailure)) {
		t.Fatalf("over-capacity observation=%+v", got)
	}
	overCapacity.Query = reservationActionQuery("9", atomicDates, 200, "after-rejection", username, password)
	got, err = model.apply(overCapacity)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, messageObservation(reservationSuccess)) {
		t.Fatalf("rejected reservation mutated capacity: %+v", got)
	}
}

func TestSequentialDifferentialAcceptsModelAndRejectsResponseMutants(t *testing.T) {
	catalog, _ := seedProfiles()
	application := &Application{catalog: catalog}

	t.Run("compatible", func(t *testing.T) {
		runtime := newDifferentialTestRuntime(t, catalog)
		checks, err := application.verifySequentialDifferential(
			context.Background(),
			client{runtime: runtime, timeout: time.Second},
			53,
			4,
		)
		if err != nil {
			t.Fatal(err)
		}
		if checks != 5+4*9 {
			t.Fatalf("checks=%d, want %d", checks, 5+4*9)
		}
	})

	tests := []struct {
		name   string
		mutate func(differentialAction, differentialObservation) differentialObservation
	}{
		{
			name: "authentication message",
			mutate: func(action differentialAction, observation differentialObservation) differentialObservation {
				if action.Kind == actionLogin {
					observation.Message = "Welcome"
				}
				return observation
			},
		},
		{
			name: "recommendation membership",
			mutate: func(action differentialAction, observation differentialObservation) differentialObservation {
				if action.Kind == actionRecommend {
					observation.HotelIDs = []string{"1"}
				}
				return observation
			},
		},
		{
			name: "search membership",
			mutate: func(action differentialAction, observation differentialObservation) differentialObservation {
				if action.Kind == actionSearch && len(observation.HotelIDs) > 0 {
					observation.HotelIDs = observation.HotelIDs[1:]
				}
				return observation
			},
		},
		{
			name: "reservation acknowledgement",
			mutate: func(action differentialAction, observation differentialObservation) differentialObservation {
				if action.Kind == actionReserve {
					observation.Message = reservationFailure
				}
				return observation
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runtime := newDifferentialTestRuntime(t, catalog)
			runtime.mutate = test.mutate
			_, err := application.verifySequentialDifferential(
				context.Background(),
				client{runtime: runtime, timeout: time.Second},
				53,
				4,
			)
			if err == nil {
				t.Fatal("semantic response mutant passed the differential check")
			}
		})
	}
}

func TestSequentialDifferentialRejectsLostWriteAndEmitsReplayablePrefix(t *testing.T) {
	catalog, _ := seedProfiles()
	application := &Application{catalog: catalog}
	runtime := newDifferentialTestRuntime(t, catalog)
	runtime.afterApply = func(action differentialAction, observation differentialObservation) {
		if runtime.writeDropped || action.Kind != actionReserve || observation.Message != reservationSuccess {
			return
		}
		runtime.model.reserved = make(map[reservationNight]int)
		runtime.writeDropped = true
	}
	_, err := application.verifySequentialDifferential(
		context.Background(),
		client{runtime: runtime, timeout: time.Second},
		71,
		4,
	)
	if err == nil {
		t.Fatal("lost acknowledged write passed the differential check")
	}

	const prefix = "Hotel sequential differential mismatch: "
	if !strings.HasPrefix(err.Error(), prefix) {
		t.Fatalf("error does not contain a replayable counterexample: %v", err)
	}
	var counterexample differentialCounterexample
	encoded := strings.TrimPrefix(err.Error(), prefix)
	if err := json.Unmarshal([]byte(encoded), &counterexample); err != nil {
		t.Fatalf("decode counterexample: %v", err)
	}
	if counterexample.SchemaVersion != 1 || counterexample.Step < 1 {
		t.Fatalf("counterexample metadata=%+v", counterexample)
	}
	if len(counterexample.History) != counterexample.Step+1 {
		t.Fatalf("history length=%d step=%d", len(counterexample.History), counterexample.Step)
	}
	if strings.Contains(encoded, `"seed"`) {
		t.Fatal("counterexample disclosed the hidden random seed")
	}

	replay, err := newSequentialModel(catalog)
	if err != nil {
		t.Fatal(err)
	}
	var replayed differentialObservation
	for _, action := range counterexample.History {
		replayed, err = replay.apply(action)
		if err != nil {
			t.Fatal(err)
		}
	}
	if !reflect.DeepEqual(replayed, counterexample.Expected) {
		t.Fatalf("replayed observation=%+v, expected %+v", replayed, counterexample.Expected)
	}
}

func containsHotelID(ids []string, wanted string) bool {
	for _, id := range ids {
		if id == wanted {
			return true
		}
	}
	return false
}

type differentialTestRuntime struct {
	t            *testing.T
	catalog      map[string]profile
	model        *sequentialModel
	mutate       func(differentialAction, differentialObservation) differentialObservation
	afterApply   func(differentialAction, differentialObservation)
	writeDropped bool
}

func newDifferentialTestRuntime(t *testing.T, catalog map[string]profile) *differentialTestRuntime {
	t.Helper()
	model, err := newSequentialModel(catalog)
	if err != nil {
		t.Fatal(err)
	}
	return &differentialTestRuntime{t: t, catalog: catalog, model: model}
}

func (r *differentialTestRuntime) Invoke(_ context.Context, invocation api.Invocation) api.ProtocolResult {
	r.t.Helper()
	spec, ok := invocation.Payload.(api.HTTPRequestSpec)
	if !ok {
		r.t.Fatalf("invocation payload=%T, want api.HTTPRequestSpec", invocation.Payload)
	}
	action := differentialAction{Query: cloneQuery(spec.Query)}
	switch spec.Path {
	case "/user":
		action.Kind = actionLogin
	case "/recommendations":
		action.Kind = actionRecommend
	case "/reservation":
		action.Kind = actionReserve
	case "/hotels":
		action.Kind = actionSearch
	default:
		r.t.Fatalf("unexpected path %q", spec.Path)
	}
	observation, err := r.model.apply(action)
	if err != nil {
		return api.ProtocolResult{
			TransportSuccess: false,
			ErrorCategory:    "test_model",
			ErrorMessage:     err.Error(),
		}
	}
	if r.afterApply != nil {
		r.afterApply(action, observation)
	}
	if r.mutate != nil {
		observation = r.mutate(action, observation)
	}
	if action.Kind == actionLogin || action.Kind == actionReserve {
		return httpResult(200, map[string]any{"message": observation.Message})
	}
	features := make([]any, 0, len(observation.HotelIDs))
	for _, id := range observation.HotelIDs {
		item := r.catalog[id]
		features = append(features, map[string]any{
			"type": "Feature",
			"id":   item.id,
			"properties": map[string]any{
				"name": item.name, "phone_number": item.phone,
			},
			"geometry": map[string]any{
				"type": "Point", "coordinates": []any{item.lon, item.lat},
			},
		})
	}
	return httpResult(200, map[string]any{"type": "FeatureCollection", "features": features})
}
