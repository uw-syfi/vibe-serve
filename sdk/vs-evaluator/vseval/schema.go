package vseval

import (
	"errors"
	"fmt"
	"strings"
	"unicode"
)

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

// ParseDir converts a configured direction word to a [Dir]. Both the wire
// forms ("max", "min") and the long forms config files usually spell
// ("maximize", "minimize") are accepted, ignoring surrounding space and case,
// so an evaluator that reads its direction from a workload config does not
// have to carry a mapping of its own.
func ParseDir(s string) (Dir, error) {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "max", "maximize":
		return Max, nil
	case "min", "minimize":
		return Min, nil
	default:
		return "", fmt.Errorf("unknown metric direction %q: want max, maximize, min, or minimize", s)
	}
}

// MetricOption is optional metadata attached to a declared metric. Construct
// one with [Unit], [Direction], or [Optional]; the zero MetricOption does
// nothing.
type MetricOption struct {
	apply func(*metricSpec)
}

// Unit sets the human-facing unit of a metric, such as "ops/s". It is never
// parsed for meaning.
func Unit(unit string) MetricOption {
	return MetricOption{apply: func(s *metricSpec) { s.Unit = unit }}
}

// Direction sets the intrinsic better-direction of a metric. Use [ParseDir]
// for a direction that comes from configuration.
func Direction(d Dir) MetricOption {
	return MetricOption{apply: func(s *metricSpec) { s.Direction = d }}
}

// Optional marks a metric the evaluator can only sometimes produce, such as a
// latency percentile that has no samples when nothing completed. [Run.Emit]
// accepts a stream in which an optional metric was never set, and rejects one
// missing any other declared metric.
//
// The metric is still declared, so a reader never sees a value under a name it
// was not told about. A task cannot rank on an optional metric: the framework
// requires every objective to be declared required, since a missing value
// would silently drop the round.
func Optional() MetricOption {
	optional := false
	return MetricOption{apply: func(s *metricSpec) { s.Required = &optional }}
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
// before declaring the schema on a report; the declaration becomes the hello
// record.
//
// A Schema is not safe for concurrent use.
type Schema struct {
	names    []string
	specs    []metricSpec
	index    map[string]int
	err      error
	declared bool
}

// NewSchema returns an empty Schema.
func NewSchema() *Schema {
	return &Schema{index: make(map[string]int)}
}

// Number declares a numeric metric and returns its handle.
//
// Declaration errors (an empty name, a name containing whitespace, a duplicate
// name, an unknown direction, or a declaration made after the hello record was
// written) are recorded on the Schema and reported by [Report.Declare], or by
// [Run.Emit], so that declarations stay assignable to a single variable. The
// returned handle is still usable; it just never reaches a stream.
func (s *Schema) Number(name string, opts ...MetricOption) Metric {
	var spec metricSpec
	for _, opt := range opts {
		if opt.apply != nil {
			opt.apply(&spec)
		}
	}
	if s.declared {
		s.setErr(fmt.Errorf("metric %q was declared after the schema: the hello record is already written", name))
	}
	if err := validateName(name); err != nil {
		s.setErr(err)
	} else if _, dup := s.index[name]; dup {
		s.setErr(fmt.Errorf("metric %q declared twice", name))
	}
	if err := validateDirection(name, spec.Direction); err != nil {
		s.setErr(err)
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

// check reports whether the schema can become a hello record.
func (s *Schema) check() error {
	if s.err != nil {
		return s.err
	}
	if len(s.names) == 0 {
		return errors.New("no metrics declared: a hello record needs at least one metric")
	}
	return nil
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

func validateDirection(name string, d Dir) error {
	switch d {
	case "", Max, Min:
		return nil
	default:
		return fmt.Errorf("metric %q declared direction %q: want %q or %q", name, d, Max, Min)
	}
}
