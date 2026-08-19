package vseval_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/uw-syfi/vibesys/sdk/vs-evaluator/vseval"
)

// TestHelloIsWrittenAtDeclare pins the crash-visibility property: the schema
// reaches the file before any measurement runs, so a reader of a truncated
// stream reports a missing outcome rather than a missing hello.
func TestHelloIsWrittenAtDeclare(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec", vseval.Unit("ops/s"), vseval.Direction(vseval.Max))
	declare(t, schema, &buf)

	records := parseStream(t, buf.Bytes())
	if len(records) != 1 || records[0]["kind"] != "hello" {
		t.Fatalf("want a single hello record, got %v", records)
	}
	if records[0]["protocol"] != float64(vseval.Protocol) {
		t.Errorf("protocol = %v, want %d", records[0]["protocol"], vseval.Protocol)
	}
	if vseval.Protocol != 2 {
		t.Errorf("Protocol = %d, want 2", vseval.Protocol)
	}
}

// TestOpeningWritesNothing is the other half of the two-phase contract: an
// open report has declared nothing, so it has put nothing on the wire.
func TestOpeningWritesNothing(t *testing.T) {
	var buf bytes.Buffer
	report := vseval.OpenWriter(&buf)
	if !report.Reporting() {
		t.Error("Reporting is false for a report opened on a writer")
	}
	if report.OutcomeWritten() {
		t.Error("OutcomeWritten is true before anything was written")
	}
	if buf.Len() != 0 {
		t.Errorf("opening wrote %q, want nothing", buf.String())
	}
}

// TestFailureBeforeAnySchemaIsAValidStream covers the port that motivated the
// split: an evaluator whose metric identity comes from a config file cannot
// declare anything until that file loads, and a failure before then must still
// reach the framework as a reason.
func TestFailureBeforeAnySchemaIsAValidStream(t *testing.T) {
	var buf bytes.Buffer
	report := vseval.OpenWriter(&buf)
	if err := report.EmitError(errors.New("workload config failed to parse")); err != nil {
		t.Fatalf("EmitError: %v", err)
	}
	records := parseStream(t, buf.Bytes())
	if len(records) != 1 {
		t.Fatalf("stream = %v, want a single error record", records)
	}
	if records[0]["kind"] != "error" || records[0]["message"] != "workload config failed to parse" {
		t.Errorf("record = %v", records[0])
	}
	if !report.OutcomeWritten() {
		t.Error("OutcomeWritten is false after an error record")
	}
	if _, err := report.Declare(vseval.NewSchema()); err == nil {
		t.Error("Declare succeeded after the stream already had an outcome")
	}
}

// TestOutcomeWrittenTracksTheStream covers the query a deferred failure
// reporter needs, so an evaluator does not have to track the outcome itself.
func TestOutcomeWrittenTracksTheStream(t *testing.T) {
	for name, finish := range map[string]func(*testing.T, *vseval.Run){
		"result": func(t *testing.T, run *vseval.Run) { mustEmit(t, run) },
		"error": func(t *testing.T, run *vseval.Run) {
			if err := run.EmitError(errors.New("queue runner exited")); err != nil {
				t.Fatalf("EmitError: %v", err)
			}
		},
	} {
		t.Run(name, func(t *testing.T) {
			var buf bytes.Buffer
			schema := vseval.NewSchema()
			ops := schema.Number("total_ops_per_sec")
			run := declare(t, schema, &buf)
			run.Set(ops, 1)
			if run.OutcomeWritten() {
				t.Error("OutcomeWritten is true after the hello record alone")
			}
			finish(t, run)
			if !run.OutcomeWritten() {
				t.Error("OutcomeWritten is false after the outcome record")
			}
			// The deferred-reporter shape: asking is what keeps a late failure
			// from writing a second outcome.
			if err := run.EmitError(errors.New("late failure")); err == nil {
				t.Error("EmitError succeeded after the stream already had an outcome")
			}
			if got := len(parseStream(t, buf.Bytes())); got != 2 {
				t.Errorf("wrote %d records, want hello and one outcome", got)
			}
		})
	}
}

// TestOptionalMetricMayBeAbsent covers the metric an evaluator can only
// sometimes produce: declared either way, absent from the row when unset.
func TestOptionalMetricMayBeAbsent(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	ops := schema.Number("operations_per_second", vseval.Direction(vseval.Max))
	schema.Number("p99_latency_ms", vseval.Unit("ms"), vseval.Optional())
	run := declare(t, schema, &buf)
	run.Set(ops, 50.9)
	mustEmit(t, run)

	records := parseStream(t, buf.Bytes())
	metrics := records[0]["metrics"].(map[string]any)
	spec := metrics["p99_latency_ms"].(map[string]any)
	if spec["required"] != false {
		t.Errorf("optional metric spec = %v, want required:false", spec)
	}
	if _, declared := metrics["operations_per_second"].(map[string]any)["required"]; declared {
		t.Error("a required metric put a required key on the wire; the protocol default is true")
	}
	values := records[1]["values"].(map[string]any)
	if _, present := values["p99_latency_ms"]; present {
		t.Errorf("values = %v, want the unset optional metric left out", values)
	}
	if values["operations_per_second"] != 50.9 {
		t.Errorf("values = %v", values)
	}
}

func TestOptionalMetricMayBePresent(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	ops := schema.Number("operations_per_second", vseval.Direction(vseval.Max))
	p99 := schema.Number("p99_latency_ms", vseval.Unit("ms"), vseval.Optional())
	run := declare(t, schema, &buf)
	run.Set(ops, 50.9)
	run.Set(p99, 812.0)
	mustEmit(t, run)

	values := parseStream(t, buf.Bytes())[1]["values"].(map[string]any)
	if values["p99_latency_ms"] != 812.0 {
		t.Errorf("values = %v, want the optional metric that was set", values)
	}
}

// TestEmitRejectsUnsetRequiredMetric keeps the zero value from passing for a
// measurement, which is the whole point of declaring metrics required.
func TestEmitRejectsUnsetRequiredMetric(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	schema.Number("p99_latency_ms")
	run := declare(t, schema, &buf)
	run.Set(ops, 1)

	err := run.Emit()
	if err == nil {
		t.Fatal("Emit succeeded with an unset required metric")
	}
	if !strings.Contains(err.Error(), "p99_latency_ms") {
		t.Errorf("error does not name the unset metric: %v", err)
	}
	if got := len(parseStream(t, buf.Bytes())); got != 1 {
		t.Errorf("wrote %d records, want only the hello record", got)
	}
}

func TestParseDir(t *testing.T) {
	valid := map[string]vseval.Dir{
		"max":        vseval.Max,
		"maximize":   vseval.Max,
		"MAXIMIZE":   vseval.Max,
		"  max  ":    vseval.Max,
		"min":        vseval.Min,
		"minimize":   vseval.Min,
		" Minimize ": vseval.Min,
	}
	for input, want := range valid {
		got, err := vseval.ParseDir(input)
		if err != nil {
			t.Errorf("ParseDir(%q): %v", input, err)
			continue
		}
		if got != want {
			t.Errorf("ParseDir(%q) = %q, want %q", input, got, want)
		}
	}
	for _, input := range []string{"", "higher", "maximise", "max ops", "up"} {
		got, err := vseval.ParseDir(input)
		if err == nil {
			t.Errorf("ParseDir(%q) = %q, want an error", input, got)
			continue
		}
		if !strings.Contains(err.Error(), "maximize") {
			t.Errorf("ParseDir(%q) error = %v, want it to list the accepted words", input, err)
		}
	}
}

func TestEmitRejectsNonFiniteValues(t *testing.T) {
	for name, value := range map[string]float64{
		"nan":  math.NaN(),
		"+inf": math.Inf(1),
		"-inf": math.Inf(-1),
	} {
		t.Run(name, func(t *testing.T) {
			var buf bytes.Buffer
			schema := vseval.NewSchema()
			ops := schema.Number("total_ops_per_sec")
			run := declare(t, schema, &buf)
			run.Set(ops, value)

			err := run.Emit()
			if err == nil {
				t.Fatal("Emit succeeded with a non-finite value")
			}
			if !strings.Contains(err.Error(), "total_ops_per_sec") {
				t.Errorf("error does not name the metric: %v", err)
			}
			if got := len(parseStream(t, buf.Bytes())); got != 1 {
				t.Errorf("wrote %d records, want only the hello record", got)
			}
		})
	}
}

func TestSetLastWriteWins(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run := declare(t, schema, &buf)
	run.Set(ops, 1)
	run.Set(ops, 2)
	mustEmit(t, run)

	records := parseStream(t, buf.Bytes())
	values := records[1]["values"].(map[string]any)
	if values["total_ops_per_sec"] != float64(2) {
		t.Errorf("total_ops_per_sec = %v, want 2 (last write wins)", values["total_ops_per_sec"])
	}
}

func TestDeclareRejectsInvalidSchema(t *testing.T) {
	cases := map[string]struct {
		declare func(*vseval.Schema)
		want    string
	}{
		"duplicate":         {func(s *vseval.Schema) { s.Number("ops"); s.Number("ops") }, "declared twice"},
		"empty name":        {func(s *vseval.Schema) { s.Number("") }, "must not be empty"},
		"inner space":       {func(s *vseval.Schema) { s.Number("total ops") }, "whitespace"},
		"tab":               {func(s *vseval.Schema) { s.Number("total\tops") }, "whitespace"},
		"trailing newline":  {func(s *vseval.Schema) { s.Number("ops\n") }, "whitespace"},
		"no metrics":        {func(s *vseval.Schema) {}, "no metrics declared"},
		"unknown direction": {func(s *vseval.Schema) { s.Number("ops", vseval.Direction("maximize")) }, "direction"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			schema := vseval.NewSchema()
			tc.declare(schema)
			var buf bytes.Buffer
			report := vseval.OpenWriter(&buf)
			_, err := report.Declare(schema)
			if err == nil {
				t.Fatal("Declare succeeded on an invalid schema")
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error = %v, want it to mention %q", err, tc.want)
			}
			if buf.Len() != 0 {
				t.Errorf("wrote %q, want nothing", buf.String())
			}
			// A rejected schema leaves the stream without an outcome, so the
			// caller can still say why the evaluator is stopping.
			if err := report.EmitError(err); err != nil {
				t.Fatalf("EmitError after a rejected Declare: %v", err)
			}
			if got := len(parseStream(t, buf.Bytes())); got != 1 {
				t.Errorf("wrote %d records, want the error record alone", got)
			}
		})
	}
}

// TestDeclareOnlyOnce guards the one-hello rule at the producer.
func TestDeclareOnlyOnce(t *testing.T) {
	var buf bytes.Buffer
	report := vseval.OpenWriter(&buf)
	first := vseval.NewSchema()
	first.Number("total_ops_per_sec")
	if _, err := report.Declare(first); err != nil {
		t.Fatalf("Declare: %v", err)
	}
	second := vseval.NewSchema()
	second.Number("p99_latency_ms")
	if _, err := report.Declare(second); err == nil {
		t.Fatal("Declare succeeded twice on one report")
	}
	if got := len(parseStream(t, buf.Bytes())); got != 1 {
		t.Errorf("wrote %d records, want one hello record", got)
	}
}

func TestSetRejectsForeignHandle(t *testing.T) {
	other := vseval.NewSchema()
	foreign := other.Number("total_ops_per_sec")

	var buf bytes.Buffer
	schema := vseval.NewSchema()
	mine := schema.Number("total_ops_per_sec")
	run := declare(t, schema, &buf)
	run.Set(foreign, 1)
	run.Set(mine, 1)

	if err := run.Emit(); err == nil {
		t.Fatal("Emit succeeded after a foreign metric handle was set")
	}
}

func TestEmitErrorWritesErrorRecord(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec")
	run := declare(t, schema, &buf)

	if err := run.EmitError(errors.New("queue runner exited")); err != nil {
		t.Fatalf("EmitError: %v", err)
	}
	records := parseStream(t, buf.Bytes())
	if len(records) != 2 {
		t.Fatalf("want hello and error records, got %v", records)
	}
	if records[1]["kind"] != "error" || records[1]["message"] != "queue runner exited" {
		t.Errorf("error record = %v", records[1])
	}
	if err := run.Emit(); err == nil {
		t.Error("Emit succeeded after the stream already had an outcome")
	}
	if got := len(parseStream(t, buf.Bytes())); got != 2 {
		t.Errorf("wrote %d records after a rejected Emit, want 2", got)
	}
}

// TestEmitErrorSubstitutesBlankMessage documents the fallback: the protocol
// requires a non-empty message, so a blank cause must not yield an invalid
// stream.
func TestEmitErrorSubstitutesBlankMessage(t *testing.T) {
	for name, cause := range map[string]error{
		"nil":   nil,
		"blank": errors.New("  "),
	} {
		t.Run(name, func(t *testing.T) {
			var buf bytes.Buffer
			report := vseval.OpenWriter(&buf)
			if err := report.EmitError(cause); err != nil {
				t.Fatalf("EmitError: %v", err)
			}
			records := parseStream(t, buf.Bytes())
			message, _ := records[0]["message"].(string)
			if message != vseval.FailureFallbackMessage {
				t.Errorf("error record message = %q, want %q", message, vseval.FailureFallbackMessage)
			}
		})
	}
}

func TestOpenPathWritesFileTheCallerCloses(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	report, err := vseval.OpenPath(path)
	if err != nil {
		t.Fatalf("OpenPath: %v", err)
	}
	defer report.Close()

	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec", vseval.Unit("ops/s"), vseval.Direction(vseval.Max))
	run, err := report.Declare(schema)
	if err != nil {
		t.Fatalf("Declare: %v", err)
	}

	// The hello record must be readable before the run ends.
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read output: %v", err)
	}
	if got := len(parseStream(t, data)); got != 1 {
		t.Fatalf("want the hello record on disk mid-run, got %d records", got)
	}

	run.Set(ops, 41250.3)
	mustEmit(t, run)
	if err := report.Close(); err != nil {
		t.Errorf("Close after Emit: %v", err)
	}

	data, err = os.ReadFile(path)
	if err != nil {
		t.Fatalf("read output: %v", err)
	}
	records := parseStream(t, data)
	if len(records) != 2 || records[1]["kind"] != "result" {
		t.Fatalf("stream = %v", records)
	}
}

// TestOpenWithoutOutputPathDiscards pins the optional-reporting contract: an
// evaluator invoked without -vs-output runs the same code and writes no file.
func TestOpenWithoutOutputPathDiscards(t *testing.T) {
	dir := t.TempDir()
	report, err := vseval.OpenPath("")
	if err != nil {
		t.Fatalf("OpenPath(\"\"): %v", err)
	}
	defer report.Close()
	if report.Reporting() {
		t.Error("Reporting is true for a report opened without an output path")
	}
	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run, err := report.Declare(schema)
	if err != nil {
		t.Fatalf("Declare: %v", err)
	}
	if run.Reporting() {
		t.Error("Run.Reporting is true for a run that reports nowhere")
	}
	run.Set(ops, 1)
	mustEmit(t, run)
	if err := report.Close(); err != nil {
		t.Errorf("Close: %v", err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("read temp dir: %v", err)
	}
	if len(entries) != 0 {
		t.Errorf("an unreported run wrote %v", entries)
	}
}

// TestUnreportedRunStillValidates keeps the discarding run honest: a missing
// measurement is a bug whether or not anyone asked for the report.
func TestUnreportedRunStillValidates(t *testing.T) {
	report, err := vseval.OpenPath("")
	if err != nil {
		t.Fatalf("OpenPath(\"\"): %v", err)
	}
	defer report.Close()
	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec")
	run, err := report.Declare(schema)
	if err != nil {
		t.Fatalf("Declare: %v", err)
	}
	if err := run.Emit(); err == nil {
		t.Fatal("Emit succeeded on an unreported run with an unset metric")
	}
}

func TestReportingIsTrueForAnOpenedFile(t *testing.T) {
	report, err := vseval.OpenPath(filepath.Join(t.TempDir(), "result.jsonl"))
	if err != nil {
		t.Fatalf("OpenPath: %v", err)
	}
	defer report.Close()
	if !report.Reporting() {
		t.Error("Reporting is false for a report that opened an output file")
	}
}

// TestOpenFlagSetRegistersAndParses covers the subcommand entry point: the
// caller owns the flag set, and the SDK adds its flag and parses the argv.
func TestOpenFlagSetRegistersAndParses(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
	duration := fs.Duration("duration", 0, "measured duration")

	report, err := vseval.OpenFlagSet(fs, []string{"--duration", "20ms", "--" + vseval.OutputFlag, path})
	if err != nil {
		t.Fatalf("OpenFlagSet: %v", err)
	}
	defer report.Close()
	if *duration != 20*time.Millisecond {
		t.Errorf("duration = %v, want 20ms: OpenFlagSet did not parse the caller's flags", *duration)
	}
	if !report.Reporting() {
		t.Error("Reporting is false for a report opened from a parsed -" + vseval.OutputFlag)
	}
	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run, err := report.Declare(schema)
	if err != nil {
		t.Fatalf("Declare: %v", err)
	}
	run.Set(ops, 1)
	mustEmit(t, run)
	if err := report.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read output: %v", err)
	}
	if got := len(parseStream(t, data)); got != 2 {
		t.Errorf("wrote %d records, want 2", got)
	}
}

// TestOpenFlagSetWithoutOutputFlag is the standalone invocation of a
// subcommand: the flag is registered but never given a value.
func TestOpenFlagSetWithoutOutputFlag(t *testing.T) {
	fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
	report, err := vseval.OpenFlagSet(fs, nil)
	if err != nil {
		t.Fatalf("OpenFlagSet: %v", err)
	}
	defer report.Close()
	if report.Reporting() {
		t.Error("Reporting is true without -" + vseval.OutputFlag)
	}
}

// TestOpenFlagSetAcceptsParsedFlagSet lets a caller that already parsed its
// own argv, to validate it before measuring, still reach the same entry point.
func TestOpenFlagSetAcceptsParsedFlagSet(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
	vseval.RegisterFlags(fs)
	if err := fs.Parse([]string{"--" + vseval.OutputFlag, path}); err != nil {
		t.Fatalf("parse flags: %v", err)
	}

	report, err := vseval.OpenFlagSet(fs, nil)
	if err != nil {
		t.Fatalf("OpenFlagSet: %v", err)
	}
	defer report.Close()
	if !report.Reporting() {
		t.Fatalf("OpenFlagSet ignored the path already parsed into the flag set")
	}
}

func TestOpenFlagSetReportsParseErrors(t *testing.T) {
	fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	if _, err := vseval.OpenFlagSet(fs, []string{"--nonexistent"}); err == nil {
		t.Fatal("OpenFlagSet succeeded on an unparseable argv")
	}
}

// TestOpenFlagSetReportsLateRegistration mirrors [TestOpenReportsLateRegistration]
// for a caller-owned flag set: a flag set parsed before the SDK saw it cannot
// carry the output path.
func TestOpenFlagSetReportsLateRegistration(t *testing.T) {
	fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
	if err := fs.Parse(nil); err != nil {
		t.Fatalf("parse flags: %v", err)
	}
	_, err := vseval.OpenFlagSet(fs, nil)
	if err == nil {
		t.Fatal("OpenFlagSet succeeded on a flag set parsed without the output flag")
	}
	if !strings.Contains(err.Error(), "RegisterFlags") {
		t.Errorf("error = %v, want it to point at RegisterFlags", err)
	}
}

func TestOpenPathUsesTheParsedOutputFlag(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	fs := flag.NewFlagSet("evaluator", flag.ContinueOnError)
	output := vseval.RegisterFlags(fs)
	if err := fs.Parse([]string{"--" + vseval.OutputFlag, path}); err != nil {
		t.Fatalf("parse flags: %v", err)
	}

	report, err := vseval.OpenPath(*output)
	if err != nil {
		t.Fatalf("OpenPath: %v", err)
	}
	defer report.Close()
	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run, err := report.Declare(schema)
	if err != nil {
		t.Fatalf("Declare: %v", err)
	}
	run.Set(ops, 1)
	mustEmit(t, run)

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read output: %v", err)
	}
	if got := len(parseStream(t, data)); got != 2 {
		t.Errorf("wrote %d records, want 2", got)
	}
}

// TestBareMetricSpecSerializesEmpty guards the omitempty tags: a metric
// declared without options must serialize as {}, not as null-valued keys.
func TestBareMetricSpecSerializesEmpty(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	schema.Number("primary_value")
	declare(t, schema, &buf)

	var hello struct {
		Metrics map[string]json.RawMessage `json:"metrics"`
	}
	line := bytes.SplitN(buf.Bytes(), []byte("\n"), 2)[0]
	if err := json.Unmarshal(line, &hello); err != nil {
		t.Fatalf("parse hello: %v", err)
	}
	if got := string(hello.Metrics["primary_value"]); got != "{}" {
		t.Errorf("bare metric spec = %s, want {}", got)
	}
}

// TestOpenReportsLateRegistration covers the guard that keeps Open from
// silently reading an unregistered flag. The test binary has already parsed
// flag.CommandLine, which is the situation an evaluator with its own flags
// lands in.
func TestOpenReportsLateRegistration(t *testing.T) {
	if !flag.Parsed() {
		t.Skip("flag.CommandLine is unparsed in this test binary")
	}
	if flag.CommandLine.Lookup(vseval.OutputFlag) != nil {
		t.Skip("-" + vseval.OutputFlag + " is already registered on flag.CommandLine")
	}
	_, err := vseval.Open()
	if err == nil {
		t.Fatal("Open succeeded without a registered output flag")
	}
	if !strings.Contains(err.Error(), "RegisterFlags") {
		t.Errorf("error = %v, want it to point at RegisterFlags or OpenPath", err)
	}
}

// TestSetRejectsMetricDeclaredAfterDeclare guards the handle bounds: a metric
// declared after the hello record is written is not in the stream's schema.
func TestSetRejectsMetricDeclaredAfterDeclare(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run := declare(t, schema, &buf)
	late := schema.Number("p99_latency_ms")

	run.Set(ops, 1)
	if err := run.Emit(); err == nil {
		t.Fatal("Emit succeeded with a metric declared after the schema")
	}

	run.Set(late, 2)
	if err := run.Emit(); err == nil {
		t.Fatal("Emit succeeded after a late metric handle was set")
	}
	if got := len(parseStream(t, buf.Bytes())); got != 1 {
		t.Errorf("wrote %d records, want only the hello record", got)
	}
}
