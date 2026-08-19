package vseval

import (
	"bytes"
	"errors"
	"strings"
	"testing"
)

// TestFailWritesErrorRecordAndExits covers the terminal failure path. exit is
// stubbed because Fail ends the process in production use.
func TestFailWritesErrorRecordAndExits(t *testing.T) {
	var buf bytes.Buffer
	schema := NewSchema()
	ops := schema.Number("total_ops_per_sec")
	run, err := schema.StartWriter(&buf)
	if err != nil {
		t.Fatalf("StartWriter: %v", err)
	}
	codes := []int{}
	run.exit = func(code int) { codes = append(codes, code) }
	run.Set(ops, 1)

	run.Fail(errors.New("vLLM server did not become ready within 300s"))

	if len(codes) != 1 || codes[0] == 0 {
		t.Errorf("exit codes = %v, want one non-zero code", codes)
	}
	lines := strings.Split(strings.TrimSpace(buf.String()), "\n")
	if len(lines) != 2 {
		t.Fatalf("stream = %q, want hello and error records", buf.String())
	}
	if !strings.Contains(lines[1], `"kind":"error"`) {
		t.Errorf("second record = %s, want an error record", lines[1])
	}
	if strings.Contains(buf.String(), `"kind":"result"`) {
		t.Error("a result record was written alongside the error record")
	}
}
