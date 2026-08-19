package vseval

import (
	"os"
	"path/filepath"
	"testing"
)

// TestEmitLeavesTheOutputOpen pins the ownership rule: the caller that opened
// the report closes it, so a deferred Close is the only close in the program
// and Emit does not race ahead of it. The check reaches into the Report because
// the difference is invisible from outside: Close is idempotent either way.
func TestEmitLeavesTheOutputOpen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	report, err := OpenPath(path)
	if err != nil {
		t.Fatalf("OpenPath: %v", err)
	}
	defer report.Close()

	schema := NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run, err := report.Declare(schema)
	if err != nil {
		t.Fatalf("Declare: %v", err)
	}
	run.Set(ops, 1)
	if err := run.Emit(); err != nil {
		t.Fatalf("Emit: %v", err)
	}
	if report.closer == nil {
		t.Fatal("Emit closed the output; closing belongs to the caller's deferred Close")
	}
	if err := report.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if err := report.Close(); err != nil {
		t.Fatalf("second Close: %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read output: %v", err)
	}
	if len(data) == 0 {
		t.Error("the emitted records did not reach the file")
	}
}

// TestEmitErrorLeavesTheOutputOpen is the failure-path twin of
// TestEmitLeavesTheOutputOpen, on a report that never declared a schema.
func TestEmitErrorLeavesTheOutputOpen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	report, err := OpenPath(path)
	if err != nil {
		t.Fatalf("OpenPath: %v", err)
	}
	defer report.Close()

	if err := report.EmitError(nil); err != nil {
		t.Fatalf("EmitError: %v", err)
	}
	if report.closer == nil {
		t.Fatal("EmitError closed the output; closing belongs to the caller's deferred Close")
	}
}
