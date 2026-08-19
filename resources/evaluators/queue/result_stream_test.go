package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// referenceBenchmarkArgs is a fast reference-candidate benchmark: the smallest
// run that still produces a real measurement.
func referenceBenchmarkArgs(workspace string, extra ...string) []string {
	args := []string{
		"--workspace", workspace,
		"--use-reference",
		"--scenario", "spsc",
		"--capacity", "32",
		"--value-size", "64",
		"--warmup", "0s",
		"--duration", "20ms",
	}
	return append(args, extra...)
}

func readStreamRecords(t *testing.T, path string) []map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	records := make([]map[string]any, 0, 2)
	for _, line := range strings.Split(string(data), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var record map[string]any
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			t.Fatalf("record %q: %v", line, err)
		}
		records = append(records, record)
	}
	return records
}

func readBenchmarkReport(t *testing.T, path string) []benchmarkResult {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var report []benchmarkResult
	if err := json.Unmarshal(data, &report); err != nil {
		t.Fatal(err)
	}
	return report
}

func TestBenchmarkStreamDeclaresAndReportsMedianRate(t *testing.T) {
	workspace := t.TempDir()
	outputs := t.TempDir()
	streamPath := filepath.Join(outputs, "stream.jsonl")
	reportPath := filepath.Join(outputs, "report.json")

	if err := runBenchmarkCommand(referenceBenchmarkArgs(
		workspace,
		"--repetitions", "3",
		"--output-json", reportPath,
		"--vs-output", streamPath,
	)); err != nil {
		t.Fatal(err)
	}

	records := readStreamRecords(t, streamPath)
	if len(records) != 2 {
		t.Fatalf("stream records = %v, want hello and result", records)
	}
	hello := records[0]
	if hello["kind"] != "hello" || hello["protocol"] != float64(2) {
		t.Fatalf("hello record = %v", hello)
	}
	metrics, ok := hello["metrics"].(map[string]any)
	if !ok || len(metrics) != 1 {
		t.Fatalf("hello metrics = %v, want exactly total_ops_per_sec", hello["metrics"])
	}
	spec, ok := metrics["total_ops_per_sec"].(map[string]any)
	if !ok {
		t.Fatalf("hello metrics = %v, want total_ops_per_sec", metrics)
	}
	if spec["unit"] != "ops/s" || spec["direction"] != "max" {
		t.Fatalf("total_ops_per_sec spec = %v", spec)
	}
	// The metric is always measured, so it is declared required, which the
	// protocol spells by leaving the key off the wire.
	if _, present := spec["required"]; present {
		t.Fatalf("total_ops_per_sec spec = %v, want no required key", spec)
	}

	result := records[1]
	if result["kind"] != "result" || result["label"] != "" {
		t.Fatalf("result record = %v", result)
	}
	values, ok := result["values"].(map[string]any)
	if !ok || len(values) != 1 {
		t.Fatalf("result values = %v, want exactly total_ops_per_sec", result["values"])
	}

	report := readBenchmarkReport(t, reportPath)
	if len(report) != 1 {
		t.Fatalf("detailed report = %v, want one scenario", report)
	}
	if values["total_ops_per_sec"] != report[0].TotalOpsPerSec {
		t.Fatalf(
			"streamed rate = %v, detailed report rate = %v",
			values["total_ops_per_sec"],
			report[0].TotalOpsPerSec,
		)
	}
	if report[0].TotalOpsPerSec <= 0 || len(report[0].TotalOpsPerSecSamples) != 3 {
		t.Fatalf("detailed report kept no median evidence: %+v", report[0])
	}
}

func TestBenchmarkStreamReportsFailureAsErrorRecord(t *testing.T) {
	workspace := t.TempDir()
	streamPath := filepath.Join(t.TempDir(), "stream.jsonl")

	// The workspace holds no candidate library, so the benchmark cannot run.
	err := runBenchmarkCommand([]string{
		"--workspace", workspace,
		"--scenario", "spsc",
		"--duration", "20ms",
		"--vs-output", streamPath,
	})
	if err == nil {
		t.Fatal("missing candidate library unexpectedly benchmarked")
	}

	records := readStreamRecords(t, streamPath)
	if len(records) != 2 {
		t.Fatalf("stream records = %v, want hello and error", records)
	}
	if records[0]["kind"] != "hello" {
		t.Fatalf("first record = %v, want hello", records[0])
	}
	if records[1]["kind"] != "error" {
		t.Fatalf("second record = %v, want error", records[1])
	}
	message, ok := records[1]["message"].(string)
	if !ok || message != err.Error() {
		t.Fatalf("error record message = %v, want %q", records[1]["message"], err)
	}
}

func TestBenchmarkStreamRejectsMultipleScenariosBeforeMeasuring(t *testing.T) {
	outputs := t.TempDir()
	streamPath := filepath.Join(outputs, "stream.jsonl")
	reportPath := filepath.Join(outputs, "report.json")

	err := runBenchmarkCommand(referenceBenchmarkArgs(
		t.TempDir(),
		"--scenario", "all",
		"--output-json", reportPath,
		"--vs-output", streamPath,
	))
	if err == nil || !strings.Contains(err.Error(), "single --scenario") {
		t.Fatalf("multi-scenario stream error = %v, want a single-scenario rejection", err)
	}
	if _, statErr := os.Stat(reportPath); statErr == nil {
		t.Fatal("rejected run still wrote a detailed report")
	}
	records := readStreamRecords(t, streamPath)
	if len(records) != 2 || records[1]["kind"] != "error" {
		t.Fatalf("stream records = %v, want hello and error", records)
	}
}

// TestBenchmarkStreamReportsBadInvocationAsErrorRecord covers the argv the
// command itself rejects. The SDK entry point parses the flag set, so --vs-output
// is already open by then and the framework receives a reason rather than a
// stream that stops after its hello record.
func TestBenchmarkStreamReportsBadInvocationAsErrorRecord(t *testing.T) {
	streamPath := filepath.Join(t.TempDir(), "stream.jsonl")

	err := runBenchmarkCommand(referenceBenchmarkArgs(
		t.TempDir(),
		"--vs-output", streamPath,
		"stray-argument",
	))
	if err == nil || !strings.Contains(err.Error(), "unexpected positional arguments") {
		t.Fatalf("bad invocation error = %v, want a positional-argument rejection", err)
	}
	records := readStreamRecords(t, streamPath)
	if len(records) != 2 || records[1]["kind"] != "error" {
		t.Fatalf("stream records = %v, want hello and error", records)
	}
}

// TestBenchmarkReportsUndecidedGateHistoryAsErrorRecord drives the whole
// command through the bounded-check path. The budget is small enough that the
// multi-producer gate history cannot be decided, so the run must reach the
// framework as a benchmark failure naming the budget rather than as a
// measurement or a hang.
func TestBenchmarkReportsUndecidedGateHistoryAsErrorRecord(t *testing.T) {
	outputs := t.TempDir()
	streamPath := filepath.Join(outputs, "stream.jsonl")
	reportPath := filepath.Join(outputs, "report.json")

	err := runBenchmarkCommand(referenceBenchmarkArgs(
		t.TempDir(),
		"--scenario", "mpsc",
		"--check-budget", "1ns",
		"--output-json", reportPath,
		"--vs-output", streamPath,
	))
	if err == nil {
		t.Fatal("undecided correctness gate still produced a measurement")
	}
	for _, want := range []string{"correctness gate", "could not be decided", "1ns"} {
		if !strings.Contains(err.Error(), want) {
			t.Fatalf("undecided gate error = %v, want it to name %q", err, want)
		}
	}
	if _, statErr := os.Stat(reportPath); statErr == nil {
		t.Fatal("undecided correctness gate still wrote a detailed report")
	}

	records := readStreamRecords(t, streamPath)
	if len(records) != 2 || records[1]["kind"] != "error" {
		t.Fatalf("stream records = %v, want hello and error", records)
	}
	if records[1]["message"] != err.Error() {
		t.Fatalf("error record message = %v, want %q", records[1]["message"], err)
	}
}

func TestBenchmarkWithoutStreamFlagKeepsExistingBehavior(t *testing.T) {
	outputs := t.TempDir()
	reportPath := filepath.Join(outputs, "report.json")

	// Two producers and two consumers keep the correctness gate's concurrent
	// history narrow. The default 4P/4C history overlaps widely enough that
	// deciding it is sometimes minutes of work, which is the gate's problem to
	// report, not this test's subject.
	if err := runBenchmarkCommand(referenceBenchmarkArgs(
		t.TempDir(),
		"--scenario", "all",
		"--producers", "2",
		"--consumers", "2",
		"--output-json", reportPath,
	)); err != nil {
		t.Fatal(err)
	}

	report := readBenchmarkReport(t, reportPath)
	if len(report) != 3 {
		t.Fatalf("detailed report = %v, want one entry per scenario", report)
	}
	for _, result := range report {
		if result.TotalOpsPerSec <= 0 {
			t.Fatalf("scenario %s measured nothing: %+v", result.Scenario, result)
		}
	}
	entries, err := os.ReadDir(outputs)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != filepath.Base(reportPath) {
		t.Fatalf("omitting --vs-output wrote extra files: %v", entries)
	}
}
