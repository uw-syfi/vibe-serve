package main

import (
	"time"

	"github.com/anishathalye/porcupine"
)

// defaultCheckBudget bounds one linearizability decision.
//
// Porcupine's search is worst-case exponential in the number of overlapping
// operations, so a concurrent history is not guaranteed to be decidable in any
// particular time. Deciding a real gate history takes milliseconds, but some
// interleavings of an MPMC history are pathological and run for minutes. An
// unbounded check turns those into a hang that the surrounding command timeout
// kills without a diagnostic.
//
// 20s is orders of magnitude above a normal decision, and it keeps the
// benchmark gate (four boundary histories plus one concurrent trial, so five
// decisions) inside the framework's 300s benchmark timeout with room for the
// measured run.
const defaultCheckBudget = 20 * time.Second

// checkScenarioHistory decides history against the scenario's correctness
// contract within budget. The verdict is tri-state: porcupine.Unknown means the
// budget expired before the search finished, which is neither a pass nor a
// proven violation.
func checkScenarioHistory(
	s scenario,
	capacity int,
	history []recordedOperation,
	budget time.Duration,
) porcupine.CheckResult {
	switch s {
	case scenarioSPSC, scenarioMPSC:
		return checkExactFIFOHistory(capacity, history, budget)
	case scenarioMPMC:
		return checkReservationAwareFIFOHistory(capacity, history, budget)
	default:
		return porcupine.Illegal
	}
}

func correctnessContract(s scenario) string {
	if s == scenarioMPMC {
		return "reservation-aware bounded FIFO"
	}
	return "linearizable bounded FIFO"
}
