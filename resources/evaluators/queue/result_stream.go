package main

import (
	"errors"
	"flag"
	"fmt"

	"github.com/uw-syfi/vibesys/sdk/vs-evaluator/vseval"
)

// benchmarkStream is the framework-facing output of the benchmark command: the
// VibeSys evaluator record stream described by sdk/vs-evaluator/PROTOCOL.md.
// It declares one metric, total_ops_per_sec, and carries either its measured
// value or the reason no value exists.
//
// Without --vs-output the SDK report discards every record. Standalone
// invocations that only want the printed summary and the --output-json report
// therefore behave exactly as before.
type benchmarkStream struct {
	report *vseval.Report
	run    *vseval.Run
	ops    vseval.Metric
}

// startBenchmarkStream registers --vs-output on flags, parses args into them,
// and writes the hello record.
//
// The metric is a constant, so both reporting phases happen here: the output
// opens as soon as the argv is parsed, and the schema is declared immediately
// after. The hello record therefore lands before the first repetition runs, so
// a crashed or timed-out benchmark still leaves a stream that names its metric.
func startBenchmarkStream(flags *flag.FlagSet, args []string) (*benchmarkStream, error) {
	report, err := vseval.OpenFlagSet(flags, args)
	if err != nil {
		return nil, err
	}
	schema := vseval.NewSchema()
	ops := schema.Number(
		"total_ops_per_sec",
		vseval.Unit("ops/s"),
		vseval.Direction(vseval.Max),
	)
	run, err := report.Declare(schema)
	if err != nil {
		// The output is already open, so a schema this command could not
		// declare still reaches the framework as a reason.
		return nil, errors.Join(err, report.EmitError(err), report.Close())
	}
	return &benchmarkStream{report: report, run: run, ops: ops}, nil
}

// requireSingleRow rejects a scenario count the stream cannot represent.
// Protocol 2 carries one result record, so a reported run measures exactly one
// scenario. An unreported run sweeps as many as it likes.
func (s *benchmarkStream) requireSingleRow(scenarios int) error {
	if !s.run.Reporting() || scenarios == 1 {
		return nil
	}
	return fmt.Errorf(
		"--%s reports one measured row, so it needs a single --scenario, not %d",
		vseval.OutputFlag,
		scenarios,
	)
}

// emit writes the result record carrying the median total_ops_per_sec of the
// measured scenario. A failure to write is reported as an error record.
func (s *benchmarkStream) emit(results []benchmarkResult) error {
	if err := s.requireSingleRow(len(results)); err != nil {
		return s.fail(err)
	}
	s.run.Set(s.ops, results[0].TotalOpsPerSec)
	if err := s.run.Emit(); err != nil {
		return s.fail(err)
	}
	return nil
}

// fail reports cause as the stream's error record and returns it unchanged, so
// the caller still exits non-zero with the same message it printed before.
func (s *benchmarkStream) fail(cause error) error {
	if err := s.run.EmitError(cause); err != nil {
		return fmt.Errorf("%w (reporting the failure also failed: %v)", cause, err)
	}
	return cause
}

// Close releases the output file. It is the only close in the command: the SDK
// leaves it to whoever opened the report.
func (s *benchmarkStream) Close() error {
	return s.report.Close()
}
