// Package hotel owns topology, seed-input, and preflight contracts shared by
// the independent Hotel benchmark and accuracy adapters.
package hotel

import (
	"fmt"
	"time"

	"vibesys/microservice-evaluator/api"
)

const GatewayTarget = "gateway"

// Strictness selects correctness properties that the pinned upstream
// DeathStarBench implementation is known to violate. They stay opt-in so the
// default gate keeps calibrating against that reference, and so a workload can
// state explicitly which stronger contract a candidate must satisfy.
type Strictness struct {
	// LinearizableCapacity requires a burst of concurrent single-room
	// reservations to accept exactly the remaining rooms.
	LinearizableCapacity bool
	// DurableAvailability requires search availability, not only the
	// reservation endpoint, to survive a candidate restart.
	DurableAvailability bool
	// EndpointLiveness requires unroutable fixture arguments to leave every
	// endpoint serving.
	EndpointLiveness bool
}

type Config struct {
	Timeout time.Duration
	Strict  Strictness
}

// ValidateTopology keeps mode-neutral workload requirements identical in
// benchmark and accuracy mode. Semantic response oracles intentionally live in
// the mode-specific packages.
func ValidateTopology(workload api.Workload) (Config, error) {
	strict, err := strictness(workload.ApplicationConfig)
	if err != nil {
		return Config{}, err
	}
	targetFound := false
	for _, target := range workload.Targets {
		if target.Name != GatewayTarget {
			continue
		}
		targetFound = true
		if target.Protocol != "http" {
			return Config{}, fmt.Errorf("Hotel gateway target must use HTTP, got %q", target.Protocol)
		}
		if target.SessionPolicy != "reuse" {
			return Config{}, fmt.Errorf("Hotel gateway target must use session_policy reuse")
		}
	}
	if !targetFound {
		return Config{}, fmt.Errorf("Hotel requires a target named %q", GatewayTarget)
	}
	if workload.Load.TimeoutSeconds <= 0 {
		return Config{}, fmt.Errorf("Hotel timeout must be positive")
	}
	return Config{
		Timeout: time.Duration(workload.Load.TimeoutSeconds * float64(time.Second)),
		Strict:  strict,
	}, nil
}

func strictness(config map[string]any) (Strictness, error) {
	strict := Strictness{}
	fields := map[string]*bool{
		"strict_linearizable_capacity": &strict.LinearizableCapacity,
		"strict_durable_availability":  &strict.DurableAvailability,
		"strict_endpoint_liveness":     &strict.EndpointLiveness,
	}
	for key, value := range config {
		field, known := fields[key]
		if !known {
			return Strictness{}, fmt.Errorf("unknown Hotel application_config field %q", key)
		}
		enabled, ok := value.(bool)
		if !ok {
			return Strictness{}, fmt.Errorf(
				"Hotel application_config field %q must be a boolean, got %T", key, value,
			)
		}
		*field = enabled
	}
	return strict, nil
}
