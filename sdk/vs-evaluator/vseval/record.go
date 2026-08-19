package vseval

// Record kinds defined by protocol 1.
const (
	kindHello  = "hello"
	kindResult = "result"
	kindError  = "error"
)

// helloRecord declares the metrics this evaluator produces. It is the first
// record of every stream.
type helloRecord struct {
	Kind     string                `json:"kind"`
	Protocol int                   `json:"protocol"`
	Metrics  map[string]metricSpec `json:"metrics"`
}

// metricSpec is the advisory metadata of one declared metric. Both fields are
// optional, so a metric declared without options serializes as {}.
type metricSpec struct {
	Unit      string `json:"unit,omitempty"`
	Direction Dir    `json:"direction,omitempty"`
}

// resultRecord carries the measured row. Its key set must equal the hello
// metric key set exactly.
type resultRecord struct {
	Kind   string             `json:"kind"`
	Label  string             `json:"label"`
	Values map[string]float64 `json:"values"`
}

// errorRecord terminates the stream. Any result in the same stream is ignored.
type errorRecord struct {
	Kind    string `json:"kind"`
	Message string `json:"message"`
}
