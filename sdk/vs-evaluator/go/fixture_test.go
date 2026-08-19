package vseval_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	vseval "github.com/uw-syfi/vibesys/sdk/vs-evaluator/go"
)

const fixtureDir = "../fixtures/valid"

// buildFixture writes the stream of one fixture through the public API.
type buildFixture func(t *testing.T, w *bytes.Buffer)

// fixtures maps every stream in fixtures/valid to the SDK calls that produce
// it. PROTOCOL.md compares records semantically, so the assertions below parse
// both sides rather than comparing bytes.
var fixtures = map[string]buildFixture{
	"bare-metric-spec.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		value := schema.Number("primary_value")
		run := start(t, schema, w)
		run.Set(value, 0.0)
		mustEmit(t, run)
	},
	"error.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		schema.Number("total_ops_per_sec", vseval.Unit("ops/s"))
		run := start(t, schema, w)
		if err := run.EmitError(errors.New("queue runner exited before the workload completed")); err != nil {
			t.Fatalf("EmitError: %v", err)
		}
	},
	"negative-and-integer-values.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		drift := schema.Number("drift_ratio")
		enqueued := schema.Number("enqueued", vseval.Unit("ops"))
		run := start(t, schema, w)
		run.Set(drift, -0.25)
		run.Set(enqueued, 1048576)
		mustEmit(t, run)
	},
	"single-metric.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		ops := schema.Number("total_ops_per_sec", vseval.Unit("ops/s"), vseval.Direction(vseval.Max))
		run := start(t, schema, w)
		run.Set(ops, 41250.3)
		mustEmit(t, run)
	},
	"two-metrics.jsonl": func(t *testing.T, w *bytes.Buffer) {
		schema := vseval.NewSchema()
		throughput := schema.Number("aggregate_throughput", vseval.Unit("tok/s"), vseval.Direction(vseval.Max))
		p99 := schema.Number("p99_latency_ms", vseval.Unit("ms"), vseval.Direction(vseval.Min))
		run := start(t, schema, w)
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
		seen++
		name := entry.Name()
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
		t.Errorf("found %d fixture files but the test declares %d", seen, len(fixtures))
	}
}

// start begins a run against buf, failing the test if the schema is invalid.
func start(t *testing.T, schema *vseval.Schema, buf *bytes.Buffer) *vseval.Run {
	t.Helper()
	run, err := schema.StartWriter(buf)
	if err != nil {
		t.Fatalf("StartWriter: %v", err)
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
