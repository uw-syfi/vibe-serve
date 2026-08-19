package vseval

import (
	"os"
	"path/filepath"
	"testing"
)

// TestEmitLeavesTheOutputOpen pins the ownership rule: the caller that started
// the run closes it, so a deferred Close is the only close in the program and
// Emit does not race ahead of it. The check reaches into the Run because the
// difference is invisible from outside: Close is idempotent either way.
func TestEmitLeavesTheOutputOpen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	schema := NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run, err := schema.StartWith(path)
	if err != nil {
		t.Fatalf("StartWith: %v", err)
	}
	defer run.Close()

	run.Set(ops, 1)
	if err := run.Emit(); err != nil {
		t.Fatalf("Emit: %v", err)
	}
	if run.closer == nil {
		t.Fatal("Emit closed the output; closing belongs to the caller's deferred Close")
	}
	if err := run.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if err := run.Close(); err != nil {
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
// TestEmitLeavesTheOutputOpen.
func TestEmitErrorLeavesTheOutputOpen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.jsonl")
	schema := NewSchema()
	schema.Number("total_ops_per_sec")
	run, err := schema.StartWith(path)
	if err != nil {
		t.Fatalf("StartWith: %v", err)
	}
	defer run.Close()

	if err := run.EmitError(nil); err != nil {
		t.Fatalf("EmitError: %v", err)
	}
	if run.closer == nil {
		t.Fatal("EmitError closed the output; closing belongs to the caller's deferred Close")
	}
}
