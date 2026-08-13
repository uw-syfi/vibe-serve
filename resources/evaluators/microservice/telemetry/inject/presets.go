package inject

import (
	"sort"
	"strconv"
)

// Runtime names a closed set of zero-code instrumentation presets.
type Runtime string

const (
	// RuntimeJaegerNative covers applications with built-in Jaeger tracing
	// (for example DeathStarBench Go and C++ services). The collector
	// impersonates the application's configured Jaeger host, so the preset
	// only tunes the sampling ratio.
	RuntimeJaegerNative Runtime = "jaeger-native"
	// RuntimeJava mounts the pinned OpenTelemetry javaagent and activates it
	// through JAVA_TOOL_OPTIONS; no image changes are required.
	RuntimeJava Runtime = "java"
	// RuntimePython configures the standard OTEL_* environment. The image
	// must already include opentelemetry-distro auto-instrumentation.
	RuntimePython Runtime = "python"
	// RuntimeNode preloads @opentelemetry/auto-instrumentations-node through
	// NODE_OPTIONS. The image must already include the package.
	RuntimeNode Runtime = "node"
)

const javaAgentMountPath = "/otel/opentelemetry-javaagent.jar"

type presetContext struct {
	serviceName          string
	collectorServiceName string
	sampleRatio          float64
	javaAgentHostPath    string
}

type presetFunc func(context presetContext) serviceFragment

var presets = map[Runtime]presetFunc{
	RuntimeJaegerNative: jaegerNativeFragment,
	RuntimeJava:         javaFragment,
	RuntimePython:       otlpEnvFragment,
	RuntimeNode:         nodeFragment,
}

func knownRuntimeNames() []string {
	names := make([]string, 0, len(presets))
	for runtime := range presets {
		names = append(names, string(runtime))
	}
	sort.Strings(names)
	return names
}

func formatRatio(ratio float64) string {
	return strconv.FormatFloat(ratio, 'g', -1, 64)
}

func jaegerNativeFragment(context presetContext) serviceFragment {
	return serviceFragment{
		Environment: map[string]string{
			"JAEGER_SAMPLE_RATIO": formatRatio(context.sampleRatio),
		},
	}
}

func otlpEnvironment(context presetContext) map[string]string {
	return map[string]string{
		"OTEL_SERVICE_NAME":           context.serviceName,
		"OTEL_EXPORTER_OTLP_ENDPOINT": "http://" + context.collectorServiceName + ":4318",
		"OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
		"OTEL_TRACES_EXPORTER":        "otlp",
		"OTEL_METRICS_EXPORTER":       "none",
		"OTEL_LOGS_EXPORTER":          "none",
		"OTEL_TRACES_SAMPLER":         "parentbased_traceidratio",
		"OTEL_TRACES_SAMPLER_ARG":     formatRatio(context.sampleRatio),
	}
}

func otlpEnvFragment(context presetContext) serviceFragment {
	return serviceFragment{Environment: otlpEnvironment(context)}
}

func javaFragment(context presetContext) serviceFragment {
	environment := otlpEnvironment(context)
	environment["JAVA_TOOL_OPTIONS"] = "-javaagent:" + javaAgentMountPath
	return serviceFragment{
		Environment: environment,
		Volumes: []string{
			context.javaAgentHostPath + ":" + javaAgentMountPath + ":ro",
		},
	}
}

func nodeFragment(context presetContext) serviceFragment {
	environment := otlpEnvironment(context)
	environment["NODE_OPTIONS"] = "--require @opentelemetry/auto-instrumentations-node/register"
	return serviceFragment{Environment: environment}
}
