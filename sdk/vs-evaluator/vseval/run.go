package vseval

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"strings"
)

// FailureFallbackMessage is the error record message written when a caller
// fails a run without a usable one. The protocol requires a non-empty message,
// so a nil or blank cause must not be allowed to produce an invalid stream.
const FailureFallbackMessage = "evaluator failed without a message"

// Run is a live evaluator report. Its hello record is already written; what
// remains is to set every declared metric and call [Run.Emit], or to report a
// failure with [Run.Fail].
//
// A Run is not safe for concurrent use.
type Run struct {
	schema    *Schema
	w         io.Writer
	closer    io.Closer
	reporting bool
	values    []float64
	set       []bool
	err       error
	done      bool
	exit      func(int)
}

// Reporting reports whether this run writes a stream anyone will read. It is
// false for a run started without an output path, which discards every record
// so that measurement code needs no branch of its own.
//
// Branch on it only for behavior that the report itself constrains. Protocol 1
// carries a single result row, for example, so an evaluator that can sweep
// several operating points has to narrow the sweep to one when it reports, and
// need not when it does not.
func (r *Run) Reporting() bool { return r.reporting }

// Set records the measured value of a declared metric.
//
// Setting the same metric twice is allowed and the last write wins;
// evaluators often refine a value as a run proceeds. Errors (a handle from a
// different schema, or a NaN or infinite value) are held and returned by
// [Run.Emit], so that measurement code stays free of error plumbing.
func (r *Run) Set(m Metric, value float64) {
	if m.schema != r.schema {
		r.setErr(fmt.Errorf("metric %q was declared on a different schema", m.name))
		return
	}
	if m.index >= len(r.values) {
		r.setErr(fmt.Errorf("metric %q was declared after the run started, so it is not in the hello record", m.name))
		return
	}
	if math.IsNaN(value) {
		r.setErr(fmt.Errorf("metric %q was set to NaN, which is not a finite JSON number", m.name))
		return
	}
	if math.IsInf(value, 0) {
		r.setErr(fmt.Errorf("metric %q was set to %v, which is not a finite JSON number", m.name, value))
		return
	}
	r.values[m.index] = value
	r.set[m.index] = true
}

// Emit writes the result record and flushes it. It does not close the output:
// the caller that started the run owns the close, normally as a deferred
// [Run.Close].
//
// It fails, writing nothing, if a declared metric was never set, or if any
// [Run.Set] call was rejected. An unset metric is an error rather than a zero:
// Go's zero value must not pass for a measurement. After a failed Emit no
// outcome has been written, so the caller can still report the failure with
// [Run.EmitError] or [Run.Fail].
func (r *Run) Emit() error {
	if r.done {
		return errors.New("the outcome record was already written")
	}
	if r.err != nil {
		return r.err
	}
	// A metric declared after the hello record was written is a schema error
	// even if its handle was never set.
	if err := r.schema.Err(); err != nil {
		return err
	}
	var missing []string
	for i, ok := range r.set {
		if !ok {
			missing = append(missing, r.schema.names[i])
		}
	}
	if len(missing) > 0 {
		return fmt.Errorf("declared metrics were never set: %s", strings.Join(missing, ", "))
	}
	values := make(map[string]float64, len(r.values))
	for i, name := range r.schema.names {
		values[name] = r.values[i]
	}
	rec := resultRecord{Kind: kindResult, Label: "", Values: values}
	if err := r.write(rec); err != nil {
		return err
	}
	r.done = true
	return nil
}

// EmitError writes an error record and flushes it; like [Run.Emit] it leaves
// the close to the caller. Use it when the evaluator cannot produce a row. A
// nil cause, or one with a blank message, is reported as
// [FailureFallbackMessage] so the stream stays valid.
func (r *Run) EmitError(cause error) error {
	if r.done {
		return errors.New("the outcome record was already written")
	}
	message := ""
	if cause != nil {
		message = strings.TrimSpace(cause.Error())
	}
	if message == "" {
		message = FailureFallbackMessage
	}
	if err := r.write(errorRecord{Kind: kindError, Message: message}); err != nil {
		return err
	}
	r.done = true
	return nil
}

// Fail writes an error record and terminates the process with a non-zero
// status. It is the terminal form of [Run.EmitError] for evaluators whose
// main function has nothing left to do; a failure to write the record is
// reported on stderr, since no one is left to receive it.
//
// Fail calls os.Exit, so no deferred function runs, including a deferred
// [Run.Close]. Use [Run.EmitError] instead wherever the caller has deferred
// cleanup or an error to return, which is every path but the outermost one.
func (r *Run) Fail(cause error) {
	if err := r.EmitError(cause); err != nil {
		fmt.Fprintf(os.Stderr, "vseval: %v\n", err)
	}
	r.exit(1)
}

// Close releases the output file if this Run opened one. Defer it right after
// the run starts: it is the only thing that closes the output, and it is
// idempotent, so a second call after an early one is harmless. A Run started
// with [Schema.StartWriter], or one that is not reporting, owns no file and
// Close is a no-op.
func (r *Run) Close() error {
	if r.closer == nil {
		return nil
	}
	closer := r.closer
	r.closer = nil
	return closer.Close()
}

func (r *Run) setErr(err error) {
	if r.err == nil {
		r.err = err
	}
}

// flusher is implemented by buffered writers such as *bufio.Writer. Records
// must reach the file as they are produced, not when the process exits.
type flusher interface {
	Flush() error
}

func (r *Run) write(record any) error {
	line, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("encode %T: %w", record, err)
	}
	if _, err := r.w.Write(append(line, '\n')); err != nil {
		return fmt.Errorf("write evaluator record: %w", err)
	}
	if f, ok := r.w.(flusher); ok {
		if err := f.Flush(); err != nil {
			return fmt.Errorf("flush evaluator record: %w", err)
		}
	}
	return nil
}
