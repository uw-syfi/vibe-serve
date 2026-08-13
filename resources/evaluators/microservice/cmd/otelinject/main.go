// Command otelinject generates a docker-compose telemetry override for a
// candidate from a declarative scenario telemetry config. It also writes the
// shared OpenTelemetry Collector configuration into the metrics directory so
// the override can mount it with an absolute path.
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"vibesys/microservice-evaluator/fsutil"
	"vibesys/microservice-evaluator/telemetry/inject"
)

const collectorConfigFileName = "otelcol-config.yaml"

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "otelinject:", err)
		os.Exit(1)
	}
}

func run() error {
	var composePath string
	var configPath string
	var metricsDir string
	var outputPath string
	flag.StringVar(&composePath, "compose", "", "candidate docker-compose file")
	flag.StringVar(&configPath, "config", "", "scenario telemetry TOML config")
	flag.StringVar(&metricsDir, "metrics-dir", "", "host directory receiving the trace capture")
	flag.StringVar(&outputPath, "output", "", "generated compose override path")
	flag.Parse()
	if composePath == "" || configPath == "" || metricsDir == "" || outputPath == "" {
		return fmt.Errorf("--compose, --config, --metrics-dir, and --output are required")
	}

	config, err := inject.LoadConfig(configPath)
	if err != nil {
		return err
	}
	composeServices, err := inject.ComposeServiceNames(composePath)
	if err != nil {
		return err
	}

	absoluteMetricsDir, err := filepath.Abs(metricsDir)
	if err != nil {
		return fmt.Errorf("resolve metrics directory: %w", err)
	}
	if err := os.MkdirAll(absoluteMetricsDir, 0o755); err != nil {
		return fmt.Errorf("create metrics directory: %w", err)
	}
	collectorConfigPath := filepath.Join(absoluteMetricsDir, collectorConfigFileName)
	if err := fsutil.WriteFileAtomic(collectorConfigPath, inject.CollectorConfigYAML, 0o644); err != nil {
		return fmt.Errorf("write collector config: %w", err)
	}

	paths := inject.Paths{
		CollectorConfig: collectorConfigPath,
		MetricsDir:      absoluteMetricsDir,
	}
	if config.JavaAgentPath != "" {
		// Relative agent paths are declared alongside the telemetry config.
		agentPath := config.JavaAgentPath
		if !filepath.IsAbs(agentPath) {
			agentPath = filepath.Join(filepath.Dir(configPath), agentPath)
		}
		if paths.JavaAgent, err = filepath.Abs(agentPath); err != nil {
			return fmt.Errorf("resolve java agent path: %w", err)
		}
		if _, err := os.Stat(paths.JavaAgent); err != nil {
			return fmt.Errorf("java agent jar: %w", err)
		}
	}

	override, warnings, err := inject.GenerateOverride(config, composeServices, paths)
	for _, warning := range warnings {
		fmt.Fprintln(os.Stderr, "otelinject: warning:", warning)
	}
	if err != nil {
		return err
	}
	if err := fsutil.WriteFileAtomic(outputPath, override, 0o644); err != nil {
		return fmt.Errorf("write override: %w", err)
	}
	return nil
}
