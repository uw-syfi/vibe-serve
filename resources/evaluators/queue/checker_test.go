package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/anishathalye/porcupine"
)

func TestAccuracyTimesOutStuckCandidateOperation(t *testing.T) {
	workspace := compileCandidateFixtureWithDefines(t, "VSQ_TEST_HANG_CAPACITY_ONE")
	previousTimeout := candidateOperationTimeout
	candidateOperationTimeout = 100 * time.Millisecond
	t.Cleanup(func() { candidateOperationTimeout = previousTimeout })

	started := time.Now()
	err := runAccuracy(accuracyConfig{
		candidateConfig: candidateConfig{
			workspace: workspace,
			candidate: "queue-candidate.so",
			scenario:  scenarioSPSC,
			capacity:  4,
			valueSize: 64,
		},
		operations:  8,
		trials:      1,
		producers:   1,
		consumers:   1,
		seed:        7,
		checkBudget: defaultCheckBudget,
	})
	if err == nil || !strings.Contains(err.Error(), "timed out") {
		t.Fatalf("stuck candidate error = %v, want operation timeout", err)
	}
	if elapsed := time.Since(started); elapsed > 2*time.Second {
		t.Fatalf("stuck candidate took %s to reject", elapsed)
	}
}

// TestAccuracyRejectsUnboundedCheckBudget pins the budget as mandatory:
// porcupine reads a zero timeout as unlimited, which is the hang the budget
// exists to prevent.
func TestAccuracyRejectsUnboundedCheckBudget(t *testing.T) {
	for _, budget := range []time.Duration{0, -time.Second} {
		err := runAccuracy(accuracyConfig{
			candidateConfig: candidateConfig{
				workspace:    t.TempDir(),
				useReference: true,
				scenario:     scenarioSPSC,
				capacity:     4,
				valueSize:    64,
			},
			operations:  8,
			trials:      1,
			producers:   1,
			consumers:   1,
			seed:        7,
			checkBudget: budget,
		})
		if err == nil || !strings.Contains(err.Error(), "check budget") {
			t.Fatalf("check budget %s error = %v, want a rejection", budget, err)
		}
	}
}

// TestGateFailureRejectsUndecidedHistory covers the verdict a bounded check
// adds. A candidate must not win by making the checker slow, so an undecided
// history fails the gate with a reason that names the scenario and the budget
// instead of claiming a violation nobody proved.
func TestGateFailureRejectsUndecidedHistory(t *testing.T) {
	config := accuracyConfig{
		candidateConfig: candidateConfig{scenario: scenarioMPMC},
		checkBudget:     250 * time.Millisecond,
	}
	if err := gateFailure(porcupine.Ok, config, "trial 0"); err != nil {
		t.Fatalf("decided-linearizable history failed the gate: %v", err)
	}
	if err := gateFailure(porcupine.Illegal, config, "trial 0"); err == nil ||
		!strings.Contains(err.Error(), "violates") {
		t.Fatalf("illegal history error = %v, want a violation", err)
	}
	err := gateFailure(porcupine.Unknown, config, "trial 0")
	if err == nil {
		t.Fatal("undecided history passed the correctness gate")
	}
	for _, want := range []string{"trial 0", "could not be decided", "mpmc", "250ms"} {
		if !strings.Contains(err.Error(), want) {
			t.Fatalf("undecided history error = %v, want it to name %q", err, want)
		}
	}
	if strings.Contains(err.Error(), "violates") {
		t.Fatalf("undecided history error = %v, want no claimed violation", err)
	}
}

func TestWorkerCountsEnforceScenarioShape(t *testing.T) {
	tests := []struct {
		scenario               scenario
		requestedP, requestedC int
		wantP, wantC           int
	}{
		{scenarioSPSC, 8, 8, 1, 1},
		{scenarioMPSC, 3, 8, 3, 1},
		{scenarioMPMC, 3, 2, 3, 2},
	}
	for _, test := range tests {
		producers, consumers, err := workerCounts(
			test.scenario,
			test.requestedP,
			test.requestedC,
		)
		if err != nil {
			t.Fatal(err)
		}
		if producers != test.wantP || consumers != test.wantC {
			t.Fatalf(
				"%s counts = %dP/%dC, want %dP/%dC",
				test.scenario,
				producers,
				consumers,
				test.wantP,
				test.wantC,
			)
		}
	}
}

func TestAccuracyCapacitiesIncludeContentionCases(t *testing.T) {
	capacities := accuracyCapacities(1024)
	want := []uint64{1024, 1, 2, 3}
	if len(capacities) != len(want) {
		t.Fatalf("capacities = %v, want %v", capacities, want)
	}
	for index := range want {
		if capacities[index] != want[index] {
			t.Fatalf("capacities = %v, want %v", capacities, want)
		}
	}

	capacities = accuracyCapacities(2)
	if len(capacities) != 3 {
		t.Fatalf("configured contention capacity was duplicated: %v", capacities)
	}
}

func TestQueueOutputRejectsWrongOperationStatus(t *testing.T) {
	_, err := queueOutputFor(
		request{operation: operationEnqueue, value: 1},
		response{status: statusValue, value: 1},
	)
	if err == nil {
		t.Fatal("enqueue accepted a dequeue response status")
	}

	_, err = queueOutputFor(
		request{operation: operationDequeue},
		response{status: statusEnqueued},
	)
	if err == nil {
		t.Fatal("dequeue accepted an enqueue response status")
	}
}

func compileCandidateFixture(t *testing.T, retainInput bool) string {
	defines := []string{}
	if retainInput {
		defines = append(defines, "VSQ_TEST_RETAIN_INPUT")
	}
	return compileCandidateFixtureWithDefines(t, defines...)
}

func compileCandidateFixtureWithDefines(t *testing.T, defines ...string) string {
	t.Helper()
	evaluatorSource, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	workspace := t.TempDir()
	library := filepath.Join(workspace, "queue-candidate.so")
	include := filepath.Join(evaluatorSource, "include")
	source := filepath.Join(evaluatorSource, "testdata", "abi_test_candidate.c")
	args := []string{"-std=c11", "-O2", "-pthread", "-I", include, source, "-o", library}
	for _, define := range defines {
		args = append([]string{"-D" + define}, args...)
	}
	if runtime.GOOS == "darwin" {
		args = append([]string{"-dynamiclib"}, args...)
	} else {
		args = append([]string{"-shared", "-fPIC"}, args...)
	}
	command := exec.Command("cc", args...)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("compile candidate fixture: %v\n%s", err, output)
	}
	return workspace
}

func TestAccuracyRejectsCandidateThatOnlySupportsMaximumLength(t *testing.T) {
	workspace := compileCandidateFixtureWithDefines(t, "VSQ_TEST_FIXED_LENGTH_ONLY")
	err := runAccuracy(accuracyConfig{
		candidateConfig: candidateConfig{
			workspace: workspace,
			candidate: "queue-candidate.so",
			scenario:  scenarioSPSC,
			capacity:  4,
			valueSize: 64,
		},
		operations:  8,
		trials:      1,
		producers:   1,
		consumers:   1,
		seed:        7,
		checkBudget: defaultCheckBudget,
	})
	if err == nil {
		t.Fatal("candidate that only supports maximum-length values passed ABI probes")
	}
}

func TestAccuracyUsesCopyingCABI(t *testing.T) {
	workspace := compileCandidateFixture(t, false)

	err := runAccuracy(accuracyConfig{
		candidateConfig: candidateConfig{
			workspace: workspace,
			candidate: "queue-candidate.so",
			scenario:  scenarioMPSC,
			capacity:  4,
			valueSize: 64,
		},
		operations:  16,
		trials:      1,
		producers:   2,
		consumers:   1,
		seed:        7,
		checkBudget: defaultCheckBudget,
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestAccuracyRejectsCandidateThatRetainsEnqueueInput(t *testing.T) {
	workspace := compileCandidateFixture(t, true)
	err := runAccuracy(accuracyConfig{
		candidateConfig: candidateConfig{
			workspace: workspace,
			candidate: "queue-candidate.so",
			scenario:  scenarioSPSC,
			capacity:  4,
			valueSize: 64,
		},
		operations:  8,
		trials:      1,
		producers:   1,
		consumers:   1,
		seed:        7,
		checkBudget: defaultCheckBudget,
	})
	if err == nil {
		t.Fatal("candidate that retained enqueue input passed copying ABI checks")
	}
}
