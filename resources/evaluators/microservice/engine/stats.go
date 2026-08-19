package engine

import (
	"math"
	"math/rand"
	"sort"
)

func distribution(values []float64) Distribution {
	result := Distribution{Count: len(values)}
	if len(values) == 0 {
		return result
	}
	ordered := append([]float64(nil), values...)
	sort.Float64s(ordered)
	var sum float64
	for _, value := range ordered {
		sum += value
	}
	result.Mean = pointer(sum / float64(len(ordered)))
	result.P50 = pointer(percentile(ordered, 50))
	result.P90 = pointer(percentile(ordered, 90))
	result.P95 = pointer(percentile(ordered, 95))
	result.P99 = pointer(percentile(ordered, 99))
	result.P999 = pointer(percentile(ordered, 99.9))
	result.Max = pointer(ordered[len(ordered)-1])
	return result
}

func percentile(ordered []float64, percent float64) float64 {
	if len(ordered) == 0 {
		return math.NaN()
	}
	if len(ordered) == 1 {
		return ordered[0]
	}
	position := percent / 100 * float64(len(ordered)-1)
	lower := int(math.Floor(position))
	upper := int(math.Ceil(position))
	if lower == upper {
		return ordered[lower]
	}
	return ordered[lower] + (ordered[upper]-ordered[lower])*(position-float64(lower))
}

func aggregate(values []float64) Aggregate {
	result := Aggregate{Trials: len(values)}
	if len(values) == 0 {
		return result
	}
	ordered := append([]float64(nil), values...)
	sort.Float64s(ordered)
	median := percentile(ordered, 50)
	deviations := make([]float64, len(ordered))
	for index, value := range ordered {
		deviations[index] = math.Abs(value - median)
	}
	sort.Float64s(deviations)
	result.Median = pointer(median)
	result.MAD = pointer(percentile(deviations, 50))
	result.IQR = pointer(percentile(ordered, 75) - percentile(ordered, 25))
	if len(ordered) >= 2 {
		result.CI95 = bootstrapMedianCI(ordered, 2000, 1)
	}
	return result
}

// LatencyMS reports the run's successful-operation latency distribution: each
// field is the median of that field across trials, which is how PrimaryValue
// aggregates a repeated measurement, and Count is the total sample count. Every
// field is nil when no trial measured a latency, which is the case for a run in
// which nothing succeeded.
func (s Summary) LatencyMS() Distribution {
	result := Distribution{}
	var mean, p50, p90, p95, p99, p999, max []float64
	for _, trial := range s.Trials {
		latency := trial.LatencyMS
		result.Count += latency.Count
		mean = appendValue(mean, latency.Mean)
		p50 = appendValue(p50, latency.P50)
		p90 = appendValue(p90, latency.P90)
		p95 = appendValue(p95, latency.P95)
		p99 = appendValue(p99, latency.P99)
		p999 = appendValue(p999, latency.P999)
		max = appendValue(max, latency.Max)
	}
	result.Mean = medianOf(mean)
	result.P50 = medianOf(p50)
	result.P90 = medianOf(p90)
	result.P95 = medianOf(p95)
	result.P99 = medianOf(p99)
	result.P999 = medianOf(p999)
	result.Max = medianOf(max)
	return result
}

func appendValue(values []float64, value *float64) []float64 {
	if value == nil {
		return values
	}
	return append(values, *value)
}

func medianOf(values []float64) *float64 {
	if len(values) == 0 {
		return nil
	}
	ordered := append([]float64(nil), values...)
	sort.Float64s(ordered)
	return pointer(percentile(ordered, 50))
}

func bootstrapMedianCI(values []float64, repetitions int, seed int64) []float64 {
	rng := rand.New(rand.NewSource(seed))
	medians := make([]float64, repetitions)
	sample := make([]float64, len(values))
	for repetition := 0; repetition < repetitions; repetition++ {
		for index := range sample {
			sample[index] = values[rng.Intn(len(values))]
		}
		sort.Float64s(sample)
		medians[repetition] = percentile(sample, 50)
	}
	sort.Float64s(medians)
	return []float64{percentile(medians, 2.5), percentile(medians, 97.5)}
}

func pointer(value float64) *float64 {
	return &value
}
