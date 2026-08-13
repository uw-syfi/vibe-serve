// Package inject generates docker-compose telemetry overrides from a
// declarative scenario config. It maps each traced compose service to a
// runtime preset (jaeger-native, java, python, node) and adds an
// OpenTelemetry Collector service that captures spans to a host-mounted
// OTLP NDJSON file for cmd/otelcapture.
package inject

import (
	"fmt"
	"sort"
	"strings"

	"github.com/BurntSushi/toml"
)

const ConfigSchemaVersion = 1

const defaultCollectorServiceName = "otel-collector"
const defaultSampleRatio = 0.1

// Config is the scenario-owned telemetry description, typically stored under
// the scenario's trusted benchmark/ directory.
type Config struct {
	Version              int               `toml:"version"`
	CollectorServiceName string            `toml:"collector_service_name"`
	SampleRatio          float64           `toml:"sample_ratio"`
	JavaAgentPath        string            `toml:"java_agent_path"`
	Services             map[string]string `toml:"services"`
}

// LoadConfig parses and validates a telemetry config file, rejecting unknown
// keys and unknown runtime names.
func LoadConfig(path string) (Config, error) {
	var config Config
	metadata, err := toml.DecodeFile(path, &config)
	if err != nil {
		return Config{}, fmt.Errorf("parse telemetry config %s: %w", path, err)
	}
	if undecoded := metadata.Undecoded(); len(undecoded) > 0 {
		keys := make([]string, 0, len(undecoded))
		for _, key := range undecoded {
			keys = append(keys, key.String())
		}
		sort.Strings(keys)
		return Config{}, fmt.Errorf(
			"telemetry config contains unknown fields: %s", strings.Join(keys, ", "))
	}
	if config.CollectorServiceName == "" {
		config.CollectorServiceName = defaultCollectorServiceName
	}
	if !metadata.IsDefined("sample_ratio") {
		config.SampleRatio = defaultSampleRatio
	}
	if err := validateConfig(config); err != nil {
		return Config{}, fmt.Errorf("telemetry config %s: %w", path, err)
	}
	return config, nil
}

func validateConfig(config Config) error {
	if config.Version != ConfigSchemaVersion {
		return fmt.Errorf("version must be %d", ConfigSchemaVersion)
	}
	if config.SampleRatio <= 0 || config.SampleRatio > 1 {
		return fmt.Errorf("sample_ratio must be in (0, 1]")
	}
	if len(config.Services) == 0 {
		return fmt.Errorf("[services] must map at least one service to a runtime")
	}
	javaUsed := false
	for _, service := range sortedServiceNames(config.Services) {
		runtime := Runtime(config.Services[service])
		if _, known := presets[runtime]; !known {
			return fmt.Errorf(
				"service %q uses unknown runtime %q (known: %s)",
				service, runtime, strings.Join(knownRuntimeNames(), ", "))
		}
		if service == config.CollectorServiceName {
			return fmt.Errorf(
				"service %q conflicts with collector_service_name", service)
		}
		if runtime == RuntimeJava {
			javaUsed = true
		}
	}
	if javaUsed && config.JavaAgentPath == "" {
		return fmt.Errorf("java_agent_path is required when a service uses the java runtime")
	}
	if !javaUsed && config.JavaAgentPath != "" {
		return fmt.Errorf("java_agent_path is set but no service uses the java runtime")
	}
	return nil
}

func sortedServiceNames(services map[string]string) []string {
	names := make([]string, 0, len(services))
	for name := range services {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}
