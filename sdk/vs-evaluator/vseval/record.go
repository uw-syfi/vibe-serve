package vseval

// Record kinds defined by the protocol.
const (
	kindHello  = "hello"
	kindResult = "result"
	kindError  = "error"
)

// helloRecord declares the metrics this evaluator produces. It is the first
// record of every stream that carries one.
type helloRecord struct {
	Kind     string                `json:"kind"`
	Protocol int                   `json:"protocol"`
	Metrics  map[string]metricSpec `json:"metrics"`
}

// metricSpec is the declared metadata of one metric. Every field is optional,
// so a metric declared without options serializes as {}. Required is a pointer
// because its protocol default is true: only an optional metric puts the key
// on the wire.
type metricSpec struct {
	Unit      string `json:"unit,omitempty"`
	Direction Dir    `json:"direction,omitempty"`
	Required  *bool  `json:"required,omitempty"`
}

// required reports whether every successful run must carry this metric.
func (s metricSpec) required() bool { return s.Required == nil || *s.Required }

// resultRecord carries the measured row. Its key set must hold every required
// hello metric and nothing the hello record did not declare.
type resultRecord struct {
	Kind   string             `json:"kind"`
	Label  string             `json:"label"`
	Values map[string]float64 `json:"values"`
}

// errorRecord terminates the stream. Any result in the same stream is ignored,
// and the record is valid with or without a preceding hello.
type errorRecord struct {
	Kind    string `json:"kind"`
	Message string `json:"message"`
}
