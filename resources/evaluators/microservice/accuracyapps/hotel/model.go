package hotel

import (
	"fmt"
	"math"
	"strconv"
	"time"

	hotelsupport "vibesys/microservice-evaluator/appsupport/hotel"
)

type reservationNight struct {
	hotelID string
	date    string
}

// sequentialModel is the deliberately simple compatibility oracle for the
// public Hotel API. It models only externally observable state and avoids the
// reference implementation's service topology, databases, and caches.
type sequentialModel struct {
	catalog  map[string]profile
	users    map[string]string
	reserved map[reservationNight]int
}

func newSequentialModel(catalog map[string]profile) (*sequentialModel, error) {
	users := make(map[string]string, 501)
	for index := 0; index <= 500; index++ {
		username, password, err := hotelsupport.User(index)
		if err != nil {
			return nil, err
		}
		users[username] = password
	}
	return &sequentialModel{
		catalog: catalog, users: users, reserved: make(map[reservationNight]int),
	}, nil
}

func (m *sequentialModel) apply(action differentialAction) (differentialObservation, error) {
	switch action.Kind {
	case actionLogin:
		return m.login(action.Query), nil
	case actionRecommend:
		return m.recommend(action.Query)
	case actionReserve:
		return m.reserve(action.Query)
	case actionSearch:
		return m.search(action.Query)
	default:
		return differentialObservation{}, fmt.Errorf("unknown Hotel differential action %q", action.Kind)
	}
}

func (m *sequentialModel) login(query map[string]string) differentialObservation {
	message := loginFailure
	if m.validCredentials(query) {
		message = loginSuccess
	}
	return messageObservation(message)
}

func (m *sequentialModel) recommend(query map[string]string) (differentialObservation, error) {
	switch query["require"] {
	case "price":
		return hotelIDsObservation("2"), nil
	case "rate":
		return hotelIDsObservation("9", "24", "39", "54", "69"), nil
	case "dis":
		lat, err := strconv.ParseFloat(query["lat"], 64)
		if err != nil {
			return differentialObservation{}, fmt.Errorf("parse recommendation latitude: %w", err)
		}
		lon, err := strconv.ParseFloat(query["lon"], 64)
		if err != nil {
			return differentialObservation{}, fmt.Errorf("parse recommendation longitude: %w", err)
		}
		nearest := ""
		nearestDistance := math.MaxFloat64
		for id, item := range m.catalog {
			distance := math.Hypot(item.recommendLat-lat, item.recommendLon-lon)
			if distance < nearestDistance {
				nearest = id
				nearestDistance = distance
			}
		}
		if nearest == "" {
			return differentialObservation{}, fmt.Errorf("Hotel catalog is empty")
		}
		return hotelIDsObservation(nearest), nil
	default:
		return differentialObservation{}, fmt.Errorf(
			"unsupported recommendation requirement %q", query["require"],
		)
	}
}

func (m *sequentialModel) reserve(query map[string]string) (differentialObservation, error) {
	hotelID := query["hotelId"]
	nights, err := reservationNights(query)
	if err != nil {
		return differentialObservation{}, err
	}
	rooms := 0
	if raw := query["number"]; raw != "" {
		rooms, err = strconv.Atoi(raw)
		if err != nil {
			return differentialObservation{}, fmt.Errorf("parse room count %q: %w", raw, err)
		}
	}
	capacity, err := capacityForHotel(hotelID)
	if err != nil {
		return differentialObservation{}, err
	}
	for _, date := range nights {
		if m.reserved[reservationNight{hotelID: hotelID, date: date}]+rooms > capacity {
			return messageObservation(reservationFailure), nil
		}
	}
	for _, date := range nights {
		m.reserved[reservationNight{hotelID: hotelID, date: date}] += rooms
	}
	if !m.validCredentials(query) {
		// The pinned frontend invokes MakeReservation even after authentication
		// fails, so a failed-login response can still consume room capacity.
		return messageObservation(loginFailure), nil
	}
	return messageObservation(reservationSuccess), nil
}

func (m *sequentialModel) search(query map[string]string) (differentialObservation, error) {
	nights, err := reservationNights(query)
	if err != nil {
		return differentialObservation{}, err
	}
	available := make([]string, 0, len(rateBackedIDs()))
	for _, hotelID := range sortedRateBackedIDs() {
		capacity, err := capacityForHotel(hotelID)
		if err != nil {
			return differentialObservation{}, err
		}
		isAvailable := true
		for _, date := range nights {
			if m.reserved[reservationNight{hotelID: hotelID, date: date}]+1 > capacity {
				isAvailable = false
				break
			}
		}
		if isAvailable {
			available = append(available, hotelID)
		}
	}
	return hotelIDsObservation(available...), nil
}

func (m *sequentialModel) validCredentials(query map[string]string) bool {
	return m.users[query["username"]] == query["password"] && query["username"] != ""
}

func reservationNights(query map[string]string) ([]string, error) {
	start, err := time.Parse(time.DateOnly, query["inDate"])
	if err != nil {
		return nil, fmt.Errorf("parse reservation start date %q: %w", query["inDate"], err)
	}
	end, err := time.Parse(time.DateOnly, query["outDate"])
	if err != nil {
		return nil, fmt.Errorf("parse reservation end date %q: %w", query["outDate"], err)
	}
	nights := make([]string, 0)
	for date := start; date.Before(end); date = date.AddDate(0, 0, 1) {
		nights = append(nights, date.Format(time.DateOnly))
	}
	return nights, nil
}

func capacityForHotel(hotelID string) (int, error) {
	numericID, err := strconv.Atoi(hotelID)
	if err != nil {
		return 0, fmt.Errorf("parse hotel ID %q: %w", hotelID, err)
	}
	return hotelsupport.Capacity(numericID)
}
