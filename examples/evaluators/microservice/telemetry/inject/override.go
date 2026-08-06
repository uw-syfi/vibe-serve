package inject

import (
	_ "embed"
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// CollectorConfigYAML is the shared OpenTelemetry Collector configuration
// written next to the capture file and mounted into the collector service.
// It accepts OTLP, Jaeger, and Zipkin traffic and appends OTLP protojson
// NDJSON that cmd/otelcapture normalizes.
//
//go:embed otelcol.yaml
var CollectorConfigYAML []byte

const collectorImage = "otel/opentelemetry-collector-contrib:0.157.0"
const collectorConfigMountPath = "/etc/otelcol/config.yaml"
const collectorDataMountPath = "/var/lib/otelcol"

// CaptureFileName is the NDJSON file the collector writes under the metrics
// directory; run commands point cmd/otelcapture --input-json at it.
const CaptureFileName = "traces.otlp.ndjson"

// Paths are host locations referenced by the generated override. They must be
// absolute so docker compose does not resolve them against the project
// directory of whichever -f file happens to come first.
type Paths struct {
	// CollectorConfig is the collector configuration file on the host.
	CollectorConfig string
	// MetricsDir is the host directory receiving the capture file.
	MetricsDir string
	// JavaAgent is the host path of opentelemetry-javaagent.jar; required
	// exactly when a service uses the java runtime.
	JavaAgent string
}

type serviceFragment struct {
	Image       string            `yaml:"image,omitempty"`
	User        string            `yaml:"user,omitempty"`
	Command     []string          `yaml:"command,omitempty"`
	Environment map[string]string `yaml:"environment,omitempty"`
	Volumes     []string          `yaml:"volumes,omitempty"`
}

type overrideFile struct {
	Services map[string]serviceFragment `yaml:"services"`
}

// ComposeServiceNames extracts the service names declared by a compose file.
func ComposeServiceNames(path string) ([]string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read compose file: %w", err)
	}
	var compose struct {
		Services map[string]yaml.Node `yaml:"services"`
	}
	if err := yaml.Unmarshal(data, &compose); err != nil {
		return nil, fmt.Errorf("parse compose file %s: %w", path, err)
	}
	if len(compose.Services) == 0 {
		return nil, fmt.Errorf("compose file %s declares no services", path)
	}
	names := make([]string, 0, len(compose.Services))
	for name := range compose.Services {
		names = append(names, name)
	}
	return names, nil
}

// GenerateOverride renders the compose override YAML for the configured
// services present in the candidate's compose file. Configured services that
// have drifted out of the compose file are skipped and reported as warnings;
// the candidate may rename or remove services while optimizing.
func GenerateOverride(config Config, composeServices []string, paths Paths) ([]byte, []string, error) {
	if err := validateConfig(config); err != nil {
		return nil, nil, err
	}
	if paths.CollectorConfig == "" || paths.MetricsDir == "" {
		return nil, nil, fmt.Errorf("collector config and metrics directory paths are required")
	}
	present := make(map[string]bool, len(composeServices))
	for _, name := range composeServices {
		present[name] = true
	}
	override := overrideFile{
		Services: map[string]serviceFragment{
			config.CollectorServiceName: {
				Image: collectorImage,
				// The contrib image runs as uid 10001, which cannot write a
				// host bind mount; the capture directory is host-owned.
				User:    "0",
				Command: []string{"--config=" + collectorConfigMountPath},
				Volumes: []string{
					paths.CollectorConfig + ":" + collectorConfigMountPath + ":ro",
					paths.MetricsDir + ":" + collectorDataMountPath,
				},
			},
		},
	}
	var warnings []string
	instrumented := 0
	for _, service := range sortedServiceNames(config.Services) {
		if !present[service] {
			warnings = append(warnings, fmt.Sprintf(
				"configured service %q is not in the compose file; skipping", service))
			continue
		}
		runtime := Runtime(config.Services[service])
		fragment := presets[runtime](presetContext{
			serviceName:          service,
			collectorServiceName: config.CollectorServiceName,
			sampleRatio:          config.SampleRatio,
			javaAgentHostPath:    paths.JavaAgent,
		})
		override.Services[service] = fragment
		instrumented++
	}
	if instrumented == 0 {
		return nil, warnings, fmt.Errorf(
			"none of the configured services exist in the compose file")
	}
	encoded, err := yaml.Marshal(override)
	if err != nil {
		return nil, warnings, fmt.Errorf("encode override: %w", err)
	}
	return encoded, warnings, nil
}
