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

// TestHelloIsWrittenAtStart pins the crash-visibility property: the schema
// reaches the file before any measurement runs, so a reader of a truncated
// stream reports a missing outcome rather than a missing hello.
func TestHelloIsWrittenAtStart(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec", vseval.Unit("ops/s"), vseval.Direction(vseval.Max))
	if _, err := schema.StartWriter(&buf); err != nil {
		t.Fatalf("StartWriter: %v", err)
	}
	records := parseStream(t, buf.Bytes())
	if len(records) != 1 || records[0]["kind"] != "hello" {
		t.Fatalf("want a single hello record, got %v", records)
	}
	if records[0]["protocol"] != float64(vseval.Protocol) {
		t.Errorf("protocol = %v, want %d", records[0]["protocol"], vseval.Protocol)
	}
}

func TestEmitRejectsUnsetMetric(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	schema.Number("p99_latency_ms")
	run := start(t, schema, &buf)
	run.Set(ops, 1)

	err := run.Emit()
	if err == nil {
		t.Fatal("Emit succeeded with an unset metric")
	}
	if !strings.Contains(err.Error(), "p99_latency_ms") {
		t.Errorf("error does not name the unset metric: %v", err)
	}
	if got := len(parseStream(t, buf.Bytes())); got != 1 {
		t.Errorf("wrote %d records, want only the hello record", got)
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
			run := start(t, schema, &buf)
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
	run := start(t, schema, &buf)
	run.Set(ops, 1)
	run.Set(ops, 2)
	mustEmit(t, run)

	records := parseStream(t, buf.Bytes())
	values := records[1]["values"].(map[string]any)
	if values["total_ops_per_sec"] != float64(2) {
		t.Errorf("total_ops_per_sec = %v, want 2 (last write wins)", values["total_ops_per_sec"])
	}
}

func TestStartRejectsInvalidDeclarations(t *testing.T) {
	cases := map[string]struct {
		declare func(*vseval.Schema)
		want    string
	}{
		"duplicate":        {func(s *vseval.Schema) { s.Number("ops"); s.Number("ops") }, "declared twice"},
		"empty name":       {func(s *vseval.Schema) { s.Number("") }, "must not be empty"},
		"inner space":      {func(s *vseval.Schema) { s.Number("total ops") }, "whitespace"},
		"tab":              {func(s *vseval.Schema) { s.Number("total\tops") }, "whitespace"},
		"trailing newline": {func(s *vseval.Schema) { s.Number("ops\n") }, "whitespace"},
		"no metrics":       {func(s *vseval.Schema) {}, "no metrics declared"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			schema := vseval.NewSchema()
			tc.declare(schema)
			var buf bytes.Buffer
			_, err := schema.StartWriter(&buf)
			if err == nil {
				t.Fatal("StartWriter succeeded on an invalid schema")
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error = %v, want it to mention %q", err, tc.want)
			}
			if buf.Len() != 0 {
				t.Errorf("wrote %q, want nothing", buf.String())
			}
		})
	}
}

func TestSetRejectsForeignHandle(t *testing.T) {
	other := vseval.NewSchema()
	foreign := other.Number("total_ops_per_sec")

	var buf bytes.Buffer
	schema := vseval.NewSchema()
	mine := schema.Number("total_ops_per_sec")
	run := start(t, schema, &buf)
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
	run := start(t, schema, &buf)

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
			schema := vseval.NewSchema()
			schema.Number("total_ops_per_sec")
			run := start(t, schema, &buf)
			if err := run.EmitError(cause); err != nil {
				t.Fatalf("EmitError: %v", err)
			}
			records := parseStream(t, buf.Bytes())
			message, _ := records[1]["message"].(string)
			if message != vseval.FailureFallbackMessage {
				t.Errorf("error record message = %q, want %q", message, vseval.FailureFallbackMessage)
			}
		})
	}
}

func TestStartWithWritesFileTheCallerCloses(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec", vseval.Unit("ops/s"), vseval.Direction(vseval.Max))
	run, err := schema.StartWith(path)
	if err != nil {
		t.Fatalf("StartWith: %v", err)
	}
	defer run.Close()

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
	if err := run.Close(); err != nil {
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

// TestStartWithoutOutputPathDiscards pins the optional-reporting contract: an
// evaluator invoked without -vs-output runs the same code and writes no file.
func TestStartWithoutOutputPathDiscards(t *testing.T) {
	dir := t.TempDir()
	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run, err := schema.StartWith("")
	if err != nil {
		t.Fatalf("StartWith(\"\"): %v", err)
	}
	defer run.Close()
	if run.Reporting() {
		t.Error("Reporting is true for a run started without an output path")
	}
	run.Set(ops, 1)
	mustEmit(t, run)
	if err := run.Close(); err != nil {
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
	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec")
	run, err := schema.StartWith("")
	if err != nil {
		t.Fatalf("StartWith(\"\"): %v", err)
	}
	defer run.Close()
	if err := run.Emit(); err == nil {
		t.Fatal("Emit succeeded on an unreported run with an unset metric")
	}
}

func TestReportingIsTrueForAnOpenedFile(t *testing.T) {
	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec")
	run, err := schema.StartWith(filepath.Join(t.TempDir(), "result.jsonl"))
	if err != nil {
		t.Fatalf("StartWith: %v", err)
	}
	defer run.Close()
	if !run.Reporting() {
		t.Error("Reporting is false for a run that opened an output file")
	}
}

// TestStartFlagSetRegistersAndParses covers the subcommand entry point: the
// caller owns the flag set, and the SDK adds its flag and parses the argv.
func TestStartFlagSetRegistersAndParses(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
	duration := fs.Duration("duration", 0, "measured duration")

	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run, err := schema.StartFlagSet(fs, []string{"--duration", "20ms", "--" + vseval.OutputFlag, path})
	if err != nil {
		t.Fatalf("StartFlagSet: %v", err)
	}
	defer run.Close()
	if *duration != 20*time.Millisecond {
		t.Errorf("duration = %v, want 20ms: StartFlagSet did not parse the caller's flags", *duration)
	}
	if !run.Reporting() {
		t.Error("Reporting is false for a run started from a parsed -" + vseval.OutputFlag)
	}
	run.Set(ops, 1)
	mustEmit(t, run)
	if err := run.Close(); err != nil {
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

// TestStartFlagSetWithoutOutputFlag is the standalone invocation of a
// subcommand: the flag is registered but never given a value.
func TestStartFlagSetWithoutOutputFlag(t *testing.T) {
	fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec")
	run, err := schema.StartFlagSet(fs, nil)
	if err != nil {
		t.Fatalf("StartFlagSet: %v", err)
	}
	defer run.Close()
	if run.Reporting() {
		t.Error("Reporting is true without -" + vseval.OutputFlag)
	}
}

// TestStartFlagSetAcceptsParsedFlagSet lets a caller that already parsed its
// own argv, to validate it before measuring, still reach the same entry point.
func TestStartFlagSetAcceptsParsedFlagSet(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
	vseval.RegisterFlags(fs)
	if err := fs.Parse([]string{"--" + vseval.OutputFlag, path}); err != nil {
		t.Fatalf("parse flags: %v", err)
	}

	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec")
	run, err := schema.StartFlagSet(fs, nil)
	if err != nil {
		t.Fatalf("StartFlagSet: %v", err)
	}
	defer run.Close()
	if !run.Reporting() {
		t.Fatalf("StartFlagSet ignored the path already parsed into the flag set")
	}
}

func TestStartFlagSetReportsParseErrors(t *testing.T) {
	fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec")
	if _, err := schema.StartFlagSet(fs, []string{"--nonexistent"}); err == nil {
		t.Fatal("StartFlagSet succeeded on an unparseable argv")
	}
}

// TestStartFlagSetReportsLateRegistration mirrors [TestStartReportsLateRegistration]
// for a caller-owned flag set: a flag set parsed before the SDK saw it cannot
// carry the output path.
func TestStartFlagSetReportsLateRegistration(t *testing.T) {
	fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
	if err := fs.Parse(nil); err != nil {
		t.Fatalf("parse flags: %v", err)
	}
	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec")
	_, err := schema.StartFlagSet(fs, nil)
	if err == nil {
		t.Fatal("StartFlagSet succeeded on a flag set parsed without the output flag")
	}
	if !strings.Contains(err.Error(), "RegisterFlags") {
		t.Errorf("error = %v, want it to point at RegisterFlags", err)
	}
}

func TestStartParsesOutputFlag(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	fs := flag.NewFlagSet("evaluator", flag.ContinueOnError)
	output := vseval.RegisterFlags(fs)
	if err := fs.Parse([]string{"--" + vseval.OutputFlag, path}); err != nil {
		t.Fatalf("parse flags: %v", err)
	}

	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run, err := schema.StartWith(*output)
	if err != nil {
		t.Fatalf("StartWith: %v", err)
	}
	defer run.Close()
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
	if _, err := schema.StartWriter(&buf); err != nil {
		t.Fatalf("StartWriter: %v", err)
	}
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

// TestStartReportsLateRegistration covers the guard that keeps Start from
// silently reading an unregistered flag. The test binary has already parsed
// flag.CommandLine, which is the situation an evaluator with its own flags
// lands in.
func TestStartReportsLateRegistration(t *testing.T) {
	if !flag.Parsed() {
		t.Skip("flag.CommandLine is unparsed in this test binary")
	}
	if flag.CommandLine.Lookup(vseval.OutputFlag) != nil {
		t.Skip("-" + vseval.OutputFlag + " is already registered on flag.CommandLine")
	}
	schema := vseval.NewSchema()
	schema.Number("total_ops_per_sec")
	_, err := schema.Start()
	if err == nil {
		t.Fatal("Start succeeded without a registered output flag")
	}
	if !strings.Contains(err.Error(), "RegisterFlags") {
		t.Errorf("error = %v, want it to point at RegisterFlags or StartWith", err)
	}
}

// TestSetRejectsMetricDeclaredAfterStart guards the handle bounds: a metric
// declared after the hello record is written is not in the stream's schema.
func TestSetRejectsMetricDeclaredAfterStart(t *testing.T) {
	var buf bytes.Buffer
	schema := vseval.NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run := start(t, schema, &buf)
	late := schema.Number("p99_latency_ms")

	run.Set(ops, 1)
	if err := run.Emit(); err == nil {
		t.Fatal("Emit succeeded with a metric declared after the run started")
	}

	run.Set(late, 2)
	if err := run.Emit(); err == nil {
		t.Fatal("Emit succeeded after a late metric handle was set")
	}
	if got := len(parseStream(t, buf.Bytes())); got != 1 {
		t.Errorf("wrote %d records, want only the hello record", got)
	}
}
