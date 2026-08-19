package main

import (
	"testing"

	"github.com/anishathalye/porcupine"
)

// The fixtures in these model tests hold a handful of operations, so the check
// budget is never the limiting factor and the verdict is effectively boolean.
// An undecided verdict means the fixture, not the model, is wrong, so these
// helpers fail the test rather than folding Unknown into either answer.

func exactFIFOAccepts(t *testing.T, capacity int, history []recordedOperation) bool {
	t.Helper()
	return decidedVerdict(t, checkExactFIFOHistory(capacity, history, defaultCheckBudget))
}

func reservationFIFOAccepts(t *testing.T, capacity int, history []recordedOperation) bool {
	t.Helper()
	return decidedVerdict(
		t,
		checkReservationAwareFIFOHistory(capacity, history, defaultCheckBudget),
	)
}

func scenarioAccepts(
	t *testing.T,
	s scenario,
	capacity int,
	history []recordedOperation,
) bool {
	t.Helper()
	return decidedVerdict(t, checkScenarioHistory(s, capacity, history, defaultCheckBudget))
}

func decidedVerdict(t *testing.T, verdict porcupine.CheckResult) bool {
	t.Helper()
	switch verdict {
	case porcupine.Ok:
		return true
	case porcupine.Illegal:
		return false
	default:
		t.Fatalf("fixture history was undecided within %s: %s", defaultCheckBudget, verdict)
		return false
	}
}

func enqueueRecord(client int, value uint64, ok bool, call int64) recordedOperation {
	return recordedOperation{
		ClientID: client,
		Input:    queueInput{Kind: "enqueue", Value: &value},
		Call:     call,
		Output:   queueOutput{EnqueueOK: &ok},
		Return:   call + 1,
	}
}

func dequeueRecord(client int, value *uint64, call int64) recordedOperation {
	output := queueOutput{DequeueNone: value == nil}
	if value != nil {
		copy := *value
		output.DequeueVal = &copy
	}
	return recordedOperation{
		ClientID: client,
		Input:    queueInput{Kind: "dequeue"},
		Call:     call,
		Output:   output,
		Return:   call + 1,
	}
}

func value(value uint64) *uint64 {
	return &value
}

func TestScenarioModelsApplyDistinctReservationSemantics(t *testing.T) {
	first := enqueueRecord(0, 10, true, 1)
	first.Return = 8
	full := enqueueRecord(1, 20, false, 3)
	empty := dequeueRecord(2, nil, 5)
	history := []recordedOperation{first, full, empty}

	if scenarioAccepts(t, scenarioSPSC, 1, history) {
		t.Fatal("exact SPSC model accepted FULL and EMPTY around an unpublished enqueue")
	}
	if scenarioAccepts(t, scenarioMPSC, 1, history) {
		t.Fatal("exact MPSC model accepted FULL and EMPTY around an unpublished enqueue")
	}
	if !scenarioAccepts(t, scenarioMPMC, 1, history) {
		t.Fatal("reservation-aware MPMC model rejected a reserved but unpublished enqueue")
	}
}

func TestModelsAgreeOnSequentialHistories(t *testing.T) {
	tests := map[string][]recordedOperation{
		"valid bounded fifo": {
			enqueueRecord(0, 10, true, 1),
			enqueueRecord(0, 20, false, 3),
			dequeueRecord(0, value(10), 5),
			dequeueRecord(0, nil, 7),
		},
		"duplicate payloads": {
			enqueueRecord(0, 10, true, 1),
			dequeueRecord(0, value(10), 3),
			enqueueRecord(0, 10, true, 5),
			dequeueRecord(0, value(10), 7),
		},
		"false full": {
			enqueueRecord(0, 10, false, 1),
		},
		"false empty": {
			enqueueRecord(0, 10, true, 1),
			dequeueRecord(0, nil, 3),
		},
		"fifo violation": {
			enqueueRecord(0, 10, true, 1),
			enqueueRecord(0, 20, true, 3),
			dequeueRecord(0, value(20), 5),
		},
	}

	for name, history := range tests {
		t.Run(name, func(t *testing.T) {
			exact := exactFIFOAccepts(t, 1, history)
			reservationAware := reservationFIFOAccepts(t, 1, history)
			if exact != reservationAware {
				t.Fatalf(
					"sequential verdicts differ: exact=%t reservation-aware=%t",
					exact,
					reservationAware,
				)
			}
		})
	}
}
