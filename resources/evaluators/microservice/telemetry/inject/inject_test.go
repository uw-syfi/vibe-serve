package inject

import (
	"flag"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

var updateGolden = flag.Bool("update", false, "rewrite golden override files")

var testPaths = Paths{
	CollectorConfig: "/workspace/metrics/otel/otelcol-config.yaml",
	MetricsDir:      "/workspace/metrics/otel",
	JavaAgent:       "/workspace/agents/opentelemetry-javaagent.jar",
}

func TestLoadConfigAppliesDefaults(t *testing.T) {
	config, err := LoadConfig(filepath.Join("testdata", "polyglot.toml"))
	if err != nil {
		t.Fatal(err)
	}
	if config.CollectorServiceName != "otel-collector" {
		t.Fatalf("collector_service_name default = %q", config.CollectorServiceName)
	}
	if config.SampleRatio != 0.25 {
		t.Fatalf("sample_ratio = %v", config.SampleRatio)
	}
}

func TestLoadConfigKeepsExplicitCollectorName(t *testing.T) {
	config, err := LoadConfig(filepath.Join("testdata", "jaeger_native.toml"))
	if err != nil {
		t.Fatal(err)
	}
	if config.CollectorServiceName != "jaeger" {
		t.Fatalf("collector_service_name = %q", config.CollectorServiceName)
	}
}

func writeConfig(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "telemetry.toml")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestLoadConfigRejectsInvalidInput(t *testing.T) {
	cases := []struct {
		name    string
		content string
		wantErr string
	}{
		{
			name:    "unknown top-level key",
			content: "version = 1\nsampl_ratio = 0.5\n[services]\nfrontend = \"jaeger-native\"\n",
			wantErr: "unknown fields: sampl_ratio",
		},
		{
			name:    "unknown runtime",
			content: "version = 1\n[services]\nfrontend = \"golang\"\n",
			wantErr: "unknown runtime \"golang\"",
		},
		{
			name:    "unsupported version",
			content: "version = 2\n[services]\nfrontend = \"jaeger-native\"\n",
			wantErr: "version must be 1",
		},
		{
			name:    "sample ratio out of range",
			content: "version = 1\nsample_ratio = 1.5\n[services]\nfrontend = \"jaeger-native\"\n",
			wantErr: "sample_ratio must be in (0, 1]",
		},
		{
			name:    "explicit zero sample ratio",
			content: "version = 1\nsample_ratio = 0.0\n[services]\nfrontend = \"jaeger-native\"\n",
			wantErr: "sample_ratio must be in (0, 1]",
		},
		{
			name:    "no services",
			content: "version = 1\n",
			wantErr: "[services] must map at least one service",
		},
		{
			name: "service conflicts with collector",
			content: "version = 1\ncollector_service_name = \"frontend\"\n" +
				"[services]\nfrontend = \"jaeger-native\"\n",
			wantErr: "conflicts with collector_service_name",
		},
		{
			name:    "java without agent path",
			content: "version = 1\n[services]\napi = \"java\"\n",
			wantErr: "java_agent_path is required",
		},
		{
			name: "agent path without java",
			content: "version = 1\njava_agent_path = \"agent.jar\"\n" +
				"[services]\nfrontend = \"jaeger-native\"\n",
			wantErr: "no service uses the java runtime",
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := LoadConfig(writeConfig(t, testCase.content))
			if err == nil || !strings.Contains(err.Error(), testCase.wantErr) {
				t.Fatalf("error = %v, want substring %q", err, testCase.wantErr)
			}
		})
	}
}

func TestComposeServiceNames(t *testing.T) {
	names, err := ComposeServiceNames(filepath.Join("testdata", "compose.yml"))
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(names)
	want := []string{"api", "frontend", "jaeger", "mongodb-search", "search", "web", "worker"}
	if strings.Join(names, ",") != strings.Join(want, ",") {
		t.Fatalf("services = %v, want %v", names, want)
	}
}

func TestComposeServiceNamesRejectsEmpty(t *testing.T) {
	path := filepath.Join(t.TempDir(), "compose.yml")
	if err := os.WriteFile(path, []byte("version: \"3.8\"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := ComposeServiceNames(path); err == nil {
		t.Fatal("expected error for compose file without services")
	}
}

func generateFromTestdata(t *testing.T, configName string) ([]byte, []string) {
	t.Helper()
	config, err := LoadConfig(filepath.Join("testdata", configName))
	if err != nil {
		t.Fatal(err)
	}
	services, err := ComposeServiceNames(filepath.Join("testdata", "compose.yml"))
	if err != nil {
		t.Fatal(err)
	}
	override, warnings, err := GenerateOverride(config, services, testPaths)
	if err != nil {
		t.Fatal(err)
	}
	return override, warnings
}

func checkGolden(t *testing.T, goldenName string, actual []byte) {
	t.Helper()
	goldenPath := filepath.Join("testdata", goldenName)
	if *updateGolden {
		if err := os.WriteFile(goldenPath, actual, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	expected, err := os.ReadFile(goldenPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(actual) != string(expected) {
		t.Fatalf("override differs from %s (run go test ./telemetry/inject -update):\n%s",
			goldenPath, actual)
	}
}

func TestGenerateOverrideJaegerNativeGolden(t *testing.T) {
	override, warnings := generateFromTestdata(t, "jaeger_native.toml")
	if len(warnings) != 0 {
		t.Fatalf("unexpected warnings: %v", warnings)
	}
	checkGolden(t, "jaeger_native_override.yaml", override)
}

func TestGenerateOverridePolyglotGolden(t *testing.T) {
	override, warnings := generateFromTestdata(t, "polyglot.toml")
	if len(warnings) != 0 {
		t.Fatalf("unexpected warnings: %v", warnings)
	}
	checkGolden(t, "polyglot_override.yaml", override)
}

func TestGenerateOverrideSkipsDriftedServices(t *testing.T) {
	config, err := LoadConfig(filepath.Join("testdata", "jaeger_native.toml"))
	if err != nil {
		t.Fatal(err)
	}
	override, warnings, err := GenerateOverride(config, []string{"frontend"}, testPaths)
	if err != nil {
		t.Fatal(err)
	}
	if len(warnings) != 1 || !strings.Contains(warnings[0], "\"search\"") {
		t.Fatalf("warnings = %v", warnings)
	}
	if strings.Contains(string(override), "search") {
		t.Fatalf("override still references drifted service:\n%s", override)
	}
}

func TestGenerateOverrideRejectsFullyDriftedConfig(t *testing.T) {
	config, err := LoadConfig(filepath.Join("testdata", "jaeger_native.toml"))
	if err != nil {
		t.Fatal(err)
	}
	_, warnings, err := GenerateOverride(config, []string{"mongodb-search"}, testPaths)
	if err == nil || !strings.Contains(err.Error(), "none of the configured services") {
		t.Fatalf("error = %v", err)
	}
	if len(warnings) != 2 {
		t.Fatalf("warnings = %v", warnings)
	}
}

func TestCollectorConfigCapturesToMetricsMount(t *testing.T) {
	content := string(CollectorConfigYAML)
	if !strings.Contains(content, "/var/lib/otelcol/"+CaptureFileName) {
		t.Fatalf("collector config does not write %s under the metrics mount", CaptureFileName)
	}
}
