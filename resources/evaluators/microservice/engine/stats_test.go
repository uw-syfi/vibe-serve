package engine

import "testing"

func TestPercentileUsesLinearInterpolation(t *testing.T) {
	values := []float64{1, 2, 3, 4}
	if got := percentile(values, 50); got != 2.5 {
		t.Fatalf("p50 = %v, want 2.5", got)
	}
}

func TestAggregateTreatsTrialsAsUnits(t *testing.T) {
	result := aggregate([]float64{10, 11, 50})
	if result.Median == nil || *result.Median != 11 {
		t.Fatalf("unexpected median: %+v", result)
	}
	if result.MAD == nil || *result.MAD != 1 {
		t.Fatalf("unexpected MAD: %+v", result)
	}
}

func TestSummaryLatencyTakesTheAcrossTrialMedian(t *testing.T) {
	summary := Summary{Trials: []TrialResult{
		{LatencyMS: Distribution{Count: 2, P50: pointer(10), P99: pointer(80)}},
		{LatencyMS: Distribution{Count: 3, P50: pointer(12), P99: pointer(95)}},
		{LatencyMS: Distribution{Count: 4, P50: pointer(11), P99: pointer(90)}},
	}}

	latency := summary.LatencyMS()
	if latency.Count != 9 {
		t.Fatalf("count = %d, want every trial's samples", latency.Count)
	}
	if latency.P50 == nil || *latency.P50 != 11 {
		t.Fatalf("p50 = %v, want the median trial's p50", latency.P50)
	}
	if latency.P99 == nil || *latency.P99 != 90 {
		t.Fatalf("p99 = %v, want the median trial's p99", latency.P99)
	}
}

func TestSummaryLatencyIsAbsentWithoutSamples(t *testing.T) {
	summary := Summary{Trials: []TrialResult{{LatencyMS: Distribution{}}}}

	latency := summary.LatencyMS()
	if latency.Count != 0 || latency.P50 != nil || latency.P99 != nil {
		t.Fatalf("latency = %+v, want no measured percentile", latency)
	}
}
