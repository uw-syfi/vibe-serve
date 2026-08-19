package vseval_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/uw-syfi/vibesys/sdk/vs-evaluator/vseval"
)

const fixtureDir = "../fixtures/valid"

// buildFixture writes the stream of one fixture through the public API.
type buildFixture func(t *testing.T, w *bytes.Buffer)

// unproducible names the fixtures this SDK is not expected to be able to
// produce, with the reason. It is not a way to excuse a stream the SDK ought to
// write: every other file in fixtures/valid must have a counterpart below.
var unproducible = map[string]string{
	// Transitional fixture kept until the Go evaluators are rebuilt against
	// this SDK. It carries protocol 1; this SDK emits protocol 2 and has no
	// way to ask for an older version, by design.
	"legacy-protocol-1.jsonl": "protocol 1 stream, superseded by single-metric.jsonl",
	// Pins a reader rule rather than a producer one: a first-position error is
	// accepted and later records are not checked. This SDK refuses to write a
	// second outcome record at all, so it cannot emit this stream by design.
	"error-first-ignores-later-records.jsonl": "reader-only rule; the SDK writes at most one outcome record",
}

// fixtures maps every stream in fixtures/valid to the SDK calls that produce
// it. PROTOCOL.md compares records semantically, so the assertions below parse
// both sides rather than comparing bytes.
var fixtures = map[string]buildFixture{
	"bare-metric-spec.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		value := schema.Number("primary_value")
		run := declare(t, schema, w)
		run.Set(value, 0.0)
		mustEmit(t, run)
	},
	"error.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		schema.Number("total_ops_per_sec", vseval.Unit("ops/s"))
		run := declare(t, schema, w)
		if err := run.EmitError(errors.New("queue runner exited before the workload completed")); err != nil {
			t.Fatalf("EmitError: %v", err)
		}
	},
	"error-without-hello.jsonl": func(t *testing.T, w *bytes.Buffer) {
		// The failure the two-phase shape exists for: the output is open, but
		// the config that names the metrics never loaded, so there is no
		// schema to declare.
		report := vseval.OpenWriter(w)
		cause := errors.New("workload config failed to parse, so no metric identity exists yet")
		if err := report.EmitError(cause); err != nil {
			t.Fatalf("EmitError: %v", err)
		}
	},
	"negative-and-integer-values.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		drift := schema.Number("drift_ratio")
		enqueued := schema.Number("enqueued", vseval.Unit("ops"))
		run := declare(t, schema, w)
		run.Set(drift, -0.25)
		run.Set(enqueued, 1048576)
		mustEmit(t, run)
	},
	"optional-metric-absent.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		ops := schema.Number("operations_per_second", vseval.Unit("operations/s"), vseval.Direction(vseval.Max))
		schema.Number("p99_latency_ms", vseval.Unit("ms"), vseval.Direction(vseval.Min), vseval.Optional())
		run := declare(t, schema, w)
		run.Set(ops, 50.9)
		mustEmit(t, run)
	},
	"optional-metric-present.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		ops := schema.Number("operations_per_second", vseval.Unit("operations/s"), vseval.Direction(vseval.Max))
		p99 := schema.Number("p99_latency_ms", vseval.Unit("ms"), vseval.Direction(vseval.Min), vseval.Optional())
		run := declare(t, schema, w)
		run.Set(ops, 50.9)
		run.Set(p99, 812.0)
		mustEmit(t, run)
	},
	"single-metric.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		ops := schema.Number("total_ops_per_sec", vseval.Unit("ops/s"), vseval.Direction(vseval.Max))
		run := declare(t, schema, w)
		run.Set(ops, 41250.3)
		mustEmit(t, run)
	},
	"two-metrics.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		throughput := schema.Number("aggregate_throughput", vseval.Unit("tok/s"), vseval.Direction(vseval.Max))
		p99 := schema.Number("p99_latency_ms", vseval.Unit("ms"), vseval.Direction(vseval.Min))
		run := declare(t, schema, w)
		run.Set(throughput, 1180.4)
		run.Set(p99, 812.0)
		mustEmit(t, run)
	},
}

func TestValidFixturesRoundTrip(t *testing.T) {
	entries, err := os.ReadDir(fixtureDir)
	if err != nil {
		t.Fatalf("read fixture dir: %v", err)
	}
	seen := 0
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".jsonl" {
			continue
		}
		name := entry.Name()
		if reason, skip := unproducible[name]; skip {
			t.Logf("skipping %s: %s", name, reason)
			continue
		}
		seen++
		build, ok := fixtures[name]
		if !ok {
			t.Errorf("fixture %s has no SDK counterpart in this test", name)
			continue
		}
		t.Run(name, func(t *testing.T) {
			var buf bytes.Buffer
			build(t, &buf)
			want := parseStream(t, readFixture(t, name))
			got := parseStream(t, buf.Bytes())
			if !reflect.DeepEqual(got, want) {
				t.Errorf("stream mismatch\n got: %v\nwant: %v", got, want)
			}
		})
	}
	if seen != len(fixtures) {
		t.Errorf("found %d producible fixture files but the test declares %d", seen, len(fixtures))
	}
}

// declare opens a report on buf and declares schema, failing the test if the
// schema is invalid.
func declare(t *testing.T, schema *vseval.Schema, buf *bytes.Buffer) *vseval.Run {
	t.Helper()
	run, err := vseval.OpenWriter(buf).Declare(schema)
	if err != nil {
		t.Fatalf("Declare: %v", err)
	}
	return run
}

func mustEmit(t *testing.T, run *vseval.Run) {
	t.Helper()
	if err := run.Emit(); err != nil {
		t.Fatalf("Emit: %v", err)
	}
}

func readFixture(t *testing.T, name string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(fixtureDir, name))
	if err != nil {
		t.Fatalf("read fixture %s: %v", name, err)
	}
	return data
}

// parseStream decodes a record stream into comparable records. Blank lines are
// ignored and key order and float formatting are irrelevant, per PROTOCOL.md.
func parseStream(t *testing.T, data []byte) []map[string]any {
	t.Helper()
	records := []map[string]any{}
	for _, line := range bytes.Split(data, []byte("\n")) {
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		var record map[string]any
		if err := json.Unmarshal(line, &record); err != nil {
			t.Fatalf("parse record %q: %v", line, err)
		}
		records = append(records, record)
	}
	return records
}
