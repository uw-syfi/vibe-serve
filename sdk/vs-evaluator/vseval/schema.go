// Package vseval writes VibeSys evaluator result streams.
//
// An evaluator declares the metrics it produces, measures them, and reports a
// single row of values. The report is a record stream: one JSON object per
// line, written to the file named by the -vs-output flag. The wire format is
// specified by sdk/vs-evaluator/PROTOCOL.md.
//
// Most evaluators are multi-command programs whose subcommands parse their own
// argv, so the typical entry point is [Schema.StartFlagSet]:
//
//	func benchmark(args []string) error {
//		fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
//		duration := fs.Duration("duration", 10*time.Second, "measured duration")
//
//		schema := vseval.NewSchema()
//		ops := schema.Number("total_ops_per_sec", vseval.Unit("ops/s"), vseval.Direction(vseval.Max))
//		p99 := schema.Number("p99_latency_ms", vseval.Unit("ms"), vseval.Direction(vseval.Min))
//
//		// Registers -vs-output on fs, parses args, writes the hello record.
//		run, err := schema.StartFlagSet(fs, args)
//		if err != nil {
//			return err
//		}
//		defer run.Close()
//
//		result, err := measure(*duration)
//		if err != nil {
//			// Report the failure on the stream and still return it, so the
//			// process exits non-zero.
//			return errors.Join(err, run.EmitError(err))
//		}
//		run.Set(ops, result.OpsPerSec)
//		run.Set(p99, result.P99Millis)
//		return run.Emit()
//	}
//
// The run owns nothing the caller must remember except the deferred
// [Run.Close]: [Run.Emit] and [Run.EmitError] write a record and flush it, and
// leave closing to the deferred call.
//
// Reporting is optional. When -vs-output is absent the returned run discards
// every record, so the same code path works for a standalone invocation;
// [Run.Reporting] tells an evaluator whether a report was actually requested.
//
// Set takes a [Metric] handle rather than a name, so a metric that was never
// declared is a compile error rather than a run-time surprise.
//
// An evaluator with a single command and no flag set of its own can use
// [Schema.Start], which does the same against flag.CommandLine.
package vseval

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"unicode"
)

// Protocol is the record stream protocol version this package writes.
const Protocol = 1

// OutputFlag is the name of the flag naming the output file. Callers spell it
// -vs-output or --vs-output; both forms are accepted by the flag package.
const OutputFlag = "vs-output"

const outputFlagUsage = "path of the VibeSys evaluator result stream to write (required)"

// Dir is the intrinsic better-direction of a metric. It is advisory metadata:
// it does not select which metrics a task optimizes for.
type Dir string

// Directions a metric can declare.
const (
	// Max marks a metric that is better when larger, such as throughput.
	Max Dir = "max"
	// Min marks a metric that is better when smaller, such as latency.
	Min Dir = "min"
)

// MetricOption is optional metadata attached to a declared metric. Construct
// one with [Unit] or [Direction]; the zero MetricOption does nothing.
type MetricOption struct {
	apply func(*metricSpec)
}

// Unit sets the human-facing unit of a metric, such as "ops/s". It is never
// parsed for meaning.
func Unit(unit string) MetricOption {
	return MetricOption{apply: func(s *metricSpec) { s.Unit = unit }}
}

// Direction sets the intrinsic better-direction of a metric.
func Direction(d Dir) MetricOption {
	return MetricOption{apply: func(s *metricSpec) { s.Direction = d }}
}

// Metric is an opaque handle to a metric declared on a [Schema]. Obtain one
// from [Schema.Number] and pass it to [Run.Set]. The zero Metric is invalid.
type Metric struct {
	schema *Schema
	index  int
	name   string
}

// Name reports the declared name of the metric.
func (m Metric) Name() string { return m.name }

// Schema is the set of metrics an evaluator produces. Declare every metric
// before starting the run; the declaration becomes the hello record.
//
// A Schema is not safe for concurrent use.
type Schema struct {
	names   []string
	specs   []metricSpec
	index   map[string]int
	err     error
	started bool
}

// NewSchema returns an empty Schema.
func NewSchema() *Schema {
	return &Schema{index: make(map[string]int)}
}

// Number declares a numeric metric and returns its handle.
//
// Declaration errors (an empty name, a name containing whitespace, a duplicate
// name, or a declaration made after the run started) are recorded on the
// Schema and reported by whichever Start method begins the run, or by
// [Run.Emit], so that declarations stay assignable to a single variable. The
// returned handle is still usable; it just never reaches a stream.
func (s *Schema) Number(name string, opts ...MetricOption) Metric {
	var spec metricSpec
	for _, opt := range opts {
		if opt.apply != nil {
			opt.apply(&spec)
		}
	}
	if s.started {
		s.setErr(fmt.Errorf("metric %q was declared after the run started: the hello record is already written", name))
	}
	if err := validateName(name); err != nil {
		s.setErr(err)
	} else if _, dup := s.index[name]; dup {
		s.setErr(fmt.Errorf("metric %q declared twice", name))
	}
	index := len(s.names)
	s.names = append(s.names, name)
	s.specs = append(s.specs, spec)
	if _, dup := s.index[name]; !dup {
		s.index[name] = index
	}
	return Metric{schema: s, index: index, name: name}
}

// Err reports the first declaration error, if any.
func (s *Schema) Err() error { return s.err }

func (s *Schema) setErr(err error) {
	if s.err == nil {
		s.err = err
	}
}

func validateName(name string) error {
	if name == "" {
		return errors.New("metric name must not be empty")
	}
	if strings.ContainsFunc(name, unicode.IsSpace) {
		return fmt.Errorf("metric name %q must not contain whitespace", name)
	}
	return nil
}

// Start parses the process flags if they are not parsed yet, opens the file
// named by -vs-output, and writes the hello record. It is the entry point for
// an evaluator with a single command; one with subcommands should use
// [Schema.StartFlagSet] instead, and one that parses its own flags should use
// [Schema.StartWith].
//
// Start registers -vs-output on flag.CommandLine. Callers that parse
// flag.CommandLine themselves must call [RegisterFlags] before their own
// flag.Parse.
func (s *Schema) Start() (*Run, error) {
	return s.StartFlagSet(flag.CommandLine, os.Args[1:])
}

// StartFlagSet starts a run against a flag set the caller owns. It is the
// entry point for an evaluator that dispatches subcommands, where each
// subcommand parses its own argv into its own [flag.FlagSet].
//
// StartFlagSet registers -vs-output on fs unless it is registered already,
// parses args into fs unless fs is parsed already, and then starts the run
// against the parsed path, as [Schema.StartWith] does. A flag set the caller
// already parsed is used as it stands and args is ignored.
//
// The hello record is written and flushed before StartFlagSet returns, not at
// [Run.Emit]. If the evaluator crashes mid-run the stream still carries the
// schema, so a reader reports a missing outcome rather than a missing hello.
func (s *Schema) StartFlagSet(fs *flag.FlagSet, args []string) (*Run, error) {
	f := fs.Lookup(OutputFlag)
	if f == nil {
		if fs.Parsed() {
			return nil, fmt.Errorf(
				"flags were parsed without -%s registered: call vseval.RegisterFlags on the flag set before parsing it, or use Schema.StartWith",
				OutputFlag,
			)
		}
		RegisterFlags(fs)
		f = fs.Lookup(OutputFlag)
	}
	if !fs.Parsed() {
		if err := fs.Parse(args); err != nil {
			return nil, err
		}
	}
	return s.StartWith(f.Value.String())
}

// RegisterFlags registers -vs-output on fs and returns the pointer that
// receives the parsed path. Use it when the evaluator owns its own flag set or
// parses flag.CommandLine itself.
func RegisterFlags(fs *flag.FlagSet) *string {
	return fs.String(OutputFlag, "", outputFlagUsage)
}

// StartWith opens path (creating or truncating it), writes the hello record,
// and returns the run. Use it when the caller already parsed the output path.
//
// An empty path means no report was requested. Reporting is optional so that
// an evaluator run by hand behaves like one run by the framework: StartWith
// returns a run that discards every record instead of an error, no file is
// created, and [Run.Reporting] reports false. An evaluator that must not be
// run unreported should check [Run.Reporting] and say so itself.
//
// The hello record is written and flushed before StartWith returns, not at
// [Run.Emit]. If the evaluator crashes mid-run the stream still carries the
// schema, so a reader reports a missing outcome rather than a missing hello.
func (s *Schema) StartWith(path string) (*Run, error) {
	if err := s.check(); err != nil {
		return nil, err
	}
	if path == "" {
		return s.start(io.Discard, nil, false)
	}
	file, err := os.Create(path)
	if err != nil {
		return nil, fmt.Errorf("open evaluator output %q: %w", path, err)
	}
	run, err := s.start(file, file, true)
	if err != nil {
		file.Close()
		return nil, err
	}
	return run, nil
}

// StartWriter writes the hello record to w and returns the run. The caller
// owns w: [Run.Close] does not close it. It is the seam tests and embedders
// use to capture a stream without touching the filesystem.
func (s *Schema) StartWriter(w io.Writer) (*Run, error) {
	if err := s.check(); err != nil {
		return nil, err
	}
	return s.start(w, nil, true)
}

func (s *Schema) check() error {
	if s.err != nil {
		return s.err
	}
	if len(s.names) == 0 {
		return errors.New("no metrics declared: a hello record needs at least one metric")
	}
	return nil
}

func (s *Schema) start(w io.Writer, closer io.Closer, reporting bool) (*Run, error) {
	metrics := make(map[string]metricSpec, len(s.names))
	for i, name := range s.names {
		metrics[name] = s.specs[i]
	}
	run := &Run{
		schema:    s,
		w:         w,
		closer:    closer,
		reporting: reporting,
		values:    make([]float64, len(s.names)),
		set:       make([]bool, len(s.names)),
		exit:      os.Exit,
	}
	rec := helloRecord{Kind: kindHello, Protocol: Protocol, Metrics: metrics}
	if err := run.write(rec); err != nil {
		return nil, err
	}
	s.started = true
	return run, nil
}
