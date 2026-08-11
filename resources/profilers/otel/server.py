"""MCP tools for normalized servicebench OpenTelemetry reports."""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, model_validator

_GRAPH_REPORT_MISMATCH = (
    "trace graph and telemetry report must have matching workload identity and windows"
)


class LatencyRow(BaseModel):  # noqa: D101  # tracked: #288
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    count: StrictInt = Field(gt=0)
    error_count: StrictInt = Field(ge=0)
    mean_ms: StrictFloat = Field(ge=0)
    p50_ms: StrictFloat = Field(ge=0)
    p95_ms: StrictFloat = Field(ge=0)
    p99_ms: StrictFloat = Field(ge=0)
    max_ms: StrictFloat = Field(ge=0)

    @model_validator(mode="after")
    def validate_distribution(self) -> "LatencyRow":  # noqa: D102  # tracked: #288
        if self.error_count > self.count:
            raise ValueError("error_count must not exceed count")  # noqa: TRY003  # tracked: #288
        values = (self.mean_ms, self.p50_ms, self.p95_ms, self.p99_ms, self.max_ms)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("latency values must be finite")  # noqa: TRY003  # tracked: #288
        if not (self.p50_ms <= self.p95_ms <= self.p99_ms <= self.max_ms):
            raise ValueError("latency percentiles must be ordered")  # noqa: TRY003  # tracked: #288
        return self


class MeasurementWindow(BaseModel):  # noqa: D101  # tracked: #288
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str

    @model_validator(mode="after")
    def validate_bounds(self) -> "MeasurementWindow":  # noqa: D102  # tracked: #288
        start = _parse_timestamp(self.start, "start")
        end = _parse_timestamp(self.end, "end")
        if end < start:
            raise ValueError("measurement window end must not precede start")  # noqa: TRY003  # tracked: #288
        return self


class LatencyDistribution(BaseModel):
    """Validated latency distribution used by schema-v2 trace graphs."""

    model_config = ConfigDict(extra="forbid")

    count: StrictInt = Field(gt=0)
    error_count: StrictInt = Field(ge=0)
    mean_ms: StrictFloat = Field(ge=0)
    p50_ms: StrictFloat = Field(ge=0)
    p95_ms: StrictFloat = Field(ge=0)
    p99_ms: StrictFloat = Field(ge=0)
    max_ms: StrictFloat = Field(ge=0)

    @model_validator(mode="after")
    def validate_distribution(self) -> "LatencyDistribution":  # noqa: D102  # tracked: #288
        if self.error_count > self.count:
            raise ValueError("error_count must not exceed count")  # noqa: TRY003  # tracked: #288
        values = (self.mean_ms, self.p50_ms, self.p95_ms, self.p99_ms, self.max_ms)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("latency values must be finite")  # noqa: TRY003  # tracked: #288
        if self.mean_ms > self.max_ms or not (
            self.p50_ms <= self.p95_ms <= self.p99_ms <= self.max_ms
        ):
            raise ValueError("latency distribution is inconsistent")  # noqa: TRY003  # tracked: #288
        return self


class TraceQuality(BaseModel):
    """Trace eligibility and correlation quality for a graph artifact."""

    model_config = ConfigDict(extra="forbid")

    captured_traces: StrictInt = Field(gt=0)
    eligible_traces: StrictInt = Field(gt=0)
    excluded_traces: StrictInt = Field(ge=0)
    exclusion_reasons: dict[str, StrictInt] = Field(default_factory=dict)
    matched_client_server_pairs: StrictInt = Field(ge=0)
    unmatched_client_spans: StrictInt = Field(ge=0)
    unmatched_server_spans: StrictInt = Field(ge=0)
    async_relationships: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "TraceQuality":  # noqa: D102  # tracked: #288
        if self.eligible_traces + self.excluded_traces != self.captured_traces:
            raise ValueError("eligible and excluded traces must equal captured traces")  # noqa: TRY003  # tracked: #288
        if any(not reason or count < 0 for reason, count in self.exclusion_reasons.items()):
            raise ValueError("exclusion reasons must have names and nonnegative counts")  # noqa: TRY003  # tracked: #288
        return self


class TraceTrialQuality(BaseModel):
    """Trace eligibility counts for one benchmark trial."""

    model_config = ConfigDict(extra="forbid")

    trial: StrictInt = Field(gt=0)
    captured_traces: StrictInt = Field(ge=0)
    eligible_traces: StrictInt = Field(ge=0)
    excluded_traces: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "TraceTrialQuality":  # noqa: D102  # tracked: #288
        if self.eligible_traces + self.excluded_traces != self.captured_traces:
            raise ValueError("trial eligible and excluded traces must equal captured traces")  # noqa: TRY003  # tracked: #288
        return self


class TraceGraphNode(BaseModel):
    """One stable service-operation path in a trace graph."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    service: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    inclusive_latency_ms: LatencyDistribution
    exclusive_latency_ms: LatencyDistribution


class TraceGraphEdge(BaseModel):
    """One observed relationship between graph nodes."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_node: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    relationship: str = Field(min_length=1)
    count: StrictInt = Field(gt=0)


class WaterfallSpan(BaseModel):
    """One span in the representative trace waterfall."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    offset_ms: StrictFloat = Field(ge=0)
    duration_ms: StrictFloat = Field(gt=0)


class RepresentativeTrace(BaseModel):
    """Representative complete trace selected by servicebench."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=1)
    duration_ms: StrictFloat = Field(gt=0)
    spans: list[WaterfallSpan] = Field(min_length=1)


class CriticalPathNodeContribution(BaseModel):
    """Wall-clock contribution attributed to one graph node."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    service: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    contribution_ms: LatencyDistribution


class CriticalPathSegment(BaseModel):
    """Contiguous segment of a representative synchronous critical path."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    offset_ms: StrictFloat = Field(ge=0)
    duration_ms: StrictFloat = Field(gt=0)


class RepresentativeCriticalPath(BaseModel):
    """Critical-path decomposition of the representative trace."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=1)
    duration_ms: StrictFloat = Field(gt=0)
    segments: list[CriticalPathSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_segments(self) -> "RepresentativeCriticalPath":  # noqa: D102  # tracked: #288
        cursor = 0.0
        for segment in self.segments:
            if not math.isclose(segment.offset_ms, cursor, abs_tol=1e-9):
                raise ValueError("critical path segments must be contiguous")  # noqa: TRY003  # tracked: #288
            cursor += segment.duration_ms
        if not math.isclose(cursor, self.duration_ms, abs_tol=1e-9):
            raise ValueError("critical path segments must cover the representative duration")  # noqa: TRY003  # tracked: #288
        return self


class CriticalPathSummary(BaseModel):
    """Aggregate synchronous critical-path evidence for one root operation."""

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["wall_clock_active_leaf_v1"]
    scope: Literal["synchronous_request"]
    trace_count: StrictInt = Field(gt=0)
    async_relationships_excluded: StrictInt = Field(ge=0)
    duration_ms: LatencyDistribution
    nodes_by_contribution: list[CriticalPathNodeContribution] = Field(min_length=1)
    representative: RepresentativeCriticalPath


class TraceRootGraph(BaseModel):
    """Aggregated trace graph and critical path for one root operation."""

    model_config = ConfigDict(extra="forbid")

    service: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    trace_count: StrictInt = Field(gt=0)
    error_count: StrictInt = Field(ge=0)
    latency_ms: LatencyDistribution
    nodes: list[TraceGraphNode] = Field(min_length=1)
    edges: list[TraceGraphEdge]
    representative_trace: RepresentativeTrace
    critical_path: CriticalPathSummary

    @model_validator(mode="after")
    def validate_graph(self) -> "TraceRootGraph":  # noqa: C901, D102  # tracked: #288
        if self.error_count > self.trace_count or self.latency_ms.count != self.trace_count:
            raise ValueError("root latency counts must match trace counts")  # noqa: TRY003  # tracked: #288
        node_by_id = {node.id: node for node in self.nodes}
        if len(node_by_id) != len(self.nodes):
            raise ValueError("trace graph node IDs must be unique")  # noqa: TRY003  # tracked: #288
        if any(
            edge.from_node not in node_by_id or edge.to not in node_by_id for edge in self.edges
        ):
            raise ValueError("trace graph edges must reference known nodes")  # noqa: TRY003  # tracked: #288
        for span in self.representative_trace.spans:
            node = node_by_id.get(span.node_id)
            if node is None or (span.service, span.operation) != (node.service, node.operation):
                raise ValueError("representative spans must match graph nodes")  # noqa: TRY003  # tracked: #288
        critical = self.critical_path
        if (
            critical.trace_count != self.trace_count
            or critical.duration_ms.count != self.trace_count
        ):
            raise ValueError("critical path counts must match root trace counts")  # noqa: TRY003  # tracked: #288
        if (critical.representative.trace_id, critical.representative.duration_ms) != (
            self.representative_trace.trace_id,
            self.representative_trace.duration_ms,
        ):
            raise ValueError("critical path must use the representative trace")  # noqa: TRY003  # tracked: #288
        contributors = {node.node_id: node for node in critical.nodes_by_contribution}
        if len(contributors) != len(critical.nodes_by_contribution):
            raise ValueError("critical path contributors must be unique")  # noqa: TRY003  # tracked: #288
        for contributor in critical.nodes_by_contribution:
            node = node_by_id.get(contributor.node_id)
            if node is None or (contributor.path, contributor.service, contributor.operation) != (
                node.path,
                node.service,
                node.operation,
            ):
                raise ValueError("critical path contributors must match graph nodes")  # noqa: TRY003  # tracked: #288
            if contributor.contribution_ms.count > self.trace_count:
                raise ValueError("critical path contribution count exceeds trace count")  # noqa: TRY003  # tracked: #288
        if any(segment.node_id not in contributors for segment in critical.representative.segments):
            raise ValueError("critical path segments must reference contributors")  # noqa: TRY003  # tracked: #288
        return self


class TraceGraphReport(BaseModel):
    """Strict schema-v2 trace graph emitted by servicebench."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    source: str = Field(min_length=1)
    collected_at: str
    workload_name: str = Field(min_length=1)
    workload_hash: str = Field(min_length=1)
    measurement_windows: list[MeasurementWindow] = Field(min_length=1)
    quality: TraceQuality
    trials: list[TraceTrialQuality] = Field(min_length=1)
    roots: list[TraceRootGraph] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_report(self) -> "TraceGraphReport":  # noqa: D102  # tracked: #288
        _parse_timestamp(self.collected_at, "collected_at")
        if len(self.trials) != len(self.measurement_windows):
            raise ValueError("trace trials must match measurement windows")  # noqa: TRY003  # tracked: #288
        if len({trial.trial for trial in self.trials}) != len(self.trials):
            raise ValueError("trace trial numbers must be unique")  # noqa: TRY003  # tracked: #288
        trial_totals = (
            sum(trial.captured_traces for trial in self.trials),
            sum(trial.eligible_traces for trial in self.trials),
            sum(trial.excluded_traces for trial in self.trials),
        )
        quality_totals = (
            self.quality.captured_traces,
            self.quality.eligible_traces,
            self.quality.excluded_traces,
        )
        if trial_totals != quality_totals:
            raise ValueError("trace trial totals must match report quality")  # noqa: TRY003  # tracked: #288
        roots = [(root.service, root.operation) for root in self.roots]
        if len(roots) != len(set(roots)):
            raise ValueError("trace roots must be unique")  # noqa: TRY003  # tracked: #288
        return self


class TelemetryReport(BaseModel):  # noqa: D101  # tracked: #288
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    source: str = Field(min_length=1)
    collected_at: str
    workload_name: str = Field(min_length=1)
    workload_hash: str = Field(min_length=1)
    measurement_windows: list[MeasurementWindow]
    span_count: StrictInt = Field(gt=0)
    error_count: StrictInt = Field(ge=0)
    services_by_p95: list[LatencyRow]
    spans_by_p95: list[LatencyRow]
    datastores_by_p95: list[LatencyRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report(self) -> "TelemetryReport":  # noqa: D102  # tracked: #288
        _parse_timestamp(self.collected_at, "collected_at")
        if not self.measurement_windows:
            raise ValueError("measurement_windows must not be empty")  # noqa: TRY003  # tracked: #288
        if self.error_count > self.span_count:
            raise ValueError("error_count must not exceed span_count")  # noqa: TRY003  # tracked: #288
        for label, rows in (
            ("services_by_p95", self.services_by_p95),
            ("spans_by_p95", self.spans_by_p95),
            ("datastores_by_p95", self.datastores_by_p95),
        ):
            names = [row.name for row in rows]
            if len(names) != len(set(names)):
                raise ValueError(f"{label} contains duplicate names")  # noqa: TRY003  # tracked: #288
        if not self.services_by_p95 or not self.spans_by_p95:
            raise ValueError("services_by_p95 and spans_by_p95 must not be empty")  # noqa: TRY003  # tracked: #288
        return self


class ReportSummary(BaseModel):
    """Ranked latency evidence returned by the ``summary`` tool."""

    model_config = ConfigDict(extra="forbid")

    source: str
    collected_at: str
    workload_name: str
    workload_hash: str
    measurement_windows: list[MeasurementWindow]
    span_count: int
    error_count: int
    services_by_p95: list[LatencyRow]
    spans_by_p95: list[LatencyRow]
    datastores_by_p95: list[LatencyRow]


class RowChange(BaseModel):
    """One service/span/datastore's p95 change between two reports.

    A ``None`` on either side means the row was absent from that report's
    top-N ranking; ``delta`` fields are ``None`` unless both sides are present.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    before_p95_ms: float | None
    after_p95_ms: float | None
    delta_p95_ms: float | None
    delta_percent: float | None


class ReportComparison(BaseModel):
    """Before/after p95 changes returned by the ``compare`` tool."""

    model_config = ConfigDict(extra="forbid")

    before_span_count: int
    after_span_count: int
    service_p95_changes: list[RowChange]
    span_p95_changes: list[RowChange]
    datastore_p95_changes: list[RowChange]


class BoundedRepresentativeCriticalPath(BaseModel):
    """Bounded representative path returned to the profiler agent."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    duration_ms: float
    segments: list[CriticalPathSegment]
    omitted_segment_count: int


class CriticalPathRootEvidence(BaseModel):
    """Bounded critical-path evidence for one root operation."""

    model_config = ConfigDict(extra="forbid")

    service: str
    operation: str
    trace_count: int
    error_count: int
    latency_ms: LatencyDistribution
    algorithm: str
    scope: str
    async_relationships_excluded: int
    duration_ms: LatencyDistribution
    nodes_by_contribution: list[CriticalPathNodeContribution]
    omitted_contributor_count: int
    representative: BoundedRepresentativeCriticalPath


class CriticalPathEvidence(BaseModel):
    """Bounded schema-v2 evidence returned by the ``critical_path`` tool."""

    model_config = ConfigDict(extra="forbid")

    source: str
    collected_at: str
    workload_name: str
    workload_hash: str
    measurement_windows: list[MeasurementWindow]
    quality: TraceQuality
    roots: list[CriticalPathRootEvidence]
    omitted_root_count: int


def load_report(path: str) -> TelemetryReport:  # noqa: D103  # tracked: #288
    return TelemetryReport.model_validate_json(Path(path).read_text(encoding="utf-8"))


def summarize_report(path: str, *, top: int = 10) -> ReportSummary:  # noqa: D103  # tracked: #288
    _validate_top(top)
    report = load_report(path)
    return ReportSummary(
        source=report.source,
        collected_at=report.collected_at,
        workload_name=report.workload_name,
        workload_hash=report.workload_hash,
        measurement_windows=report.measurement_windows,
        span_count=report.span_count,
        error_count=report.error_count,
        services_by_p95=report.services_by_p95[:top],
        spans_by_p95=report.spans_by_p95[:top],
        datastores_by_p95=report.datastores_by_p95[:top],
    )


def compare_reports(before_path: str, after_path: str, *, top: int = 10) -> ReportComparison:  # noqa: D103  # tracked: #288
    _validate_top(top)
    before = load_report(before_path)
    after = load_report(after_path)
    if _report_identity(before) != _report_identity(after):
        raise ValueError("reports must have matching workload identity and window count")  # noqa: TRY003  # tracked: #288
    return ReportComparison(
        before_span_count=before.span_count,
        after_span_count=after.span_count,
        service_p95_changes=_compare_rows(before.services_by_p95, after.services_by_p95, top),
        span_p95_changes=_compare_rows(before.spans_by_p95, after.spans_by_p95, top),
        datastore_p95_changes=_compare_rows(before.datastores_by_p95, after.datastores_by_p95, top),
    )


def load_trace_graph(path: str) -> TraceGraphReport:
    """Load and strictly validate one servicebench schema-v2 trace graph."""
    return TraceGraphReport.model_validate_json(Path(path).read_text(encoding="utf-8"))


def summarize_critical_path(
    path: str, telemetry_path: str, *, top: int = 10
) -> CriticalPathEvidence:
    """Return bounded critical-path evidence from a schema-v2 trace graph."""
    _validate_top(top)
    graph = load_trace_graph(path)
    telemetry = load_report(telemetry_path)
    if (
        graph.workload_name,
        graph.workload_hash,
        graph.measurement_windows,
    ) != (
        telemetry.workload_name,
        telemetry.workload_hash,
        telemetry.measurement_windows,
    ):
        raise ValueError(_GRAPH_REPORT_MISMATCH)
    roots = []
    for root in graph.roots[:top]:
        critical = root.critical_path
        representative = critical.representative
        roots.append(
            CriticalPathRootEvidence(
                service=root.service,
                operation=root.operation,
                trace_count=root.trace_count,
                error_count=root.error_count,
                latency_ms=root.latency_ms,
                algorithm=critical.algorithm,
                scope=critical.scope,
                async_relationships_excluded=critical.async_relationships_excluded,
                duration_ms=critical.duration_ms,
                nodes_by_contribution=critical.nodes_by_contribution[:top],
                omitted_contributor_count=max(len(critical.nodes_by_contribution) - top, 0),
                representative=BoundedRepresentativeCriticalPath(
                    trace_id=representative.trace_id,
                    duration_ms=representative.duration_ms,
                    segments=representative.segments[:top],
                    omitted_segment_count=max(len(representative.segments) - top, 0),
                ),
            )
        )
    return CriticalPathEvidence(
        source=graph.source,
        collected_at=graph.collected_at,
        workload_name=graph.workload_name,
        workload_hash=graph.workload_hash,
        measurement_windows=graph.measurement_windows,
        quality=graph.quality,
        roots=roots,
        omitted_root_count=max(len(graph.roots) - top, 0),
    )


def _report_identity(report: TelemetryReport) -> tuple:
    return (
        report.workload_name,
        report.workload_hash,
        len(report.measurement_windows),
    )


def _parse_timestamp(value: str, label: str) -> datetime:
    if not value:
        raise ValueError(f"measurement window {label} must not be empty")  # noqa: TRY003  # tracked: #288
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: FURB162  # tracked: #288
    except ValueError as exc:
        raise ValueError(f"measurement window {label} must be an RFC3339 timestamp") from exc  # noqa: TRY003  # tracked: #288
    if timestamp.tzinfo is None:
        raise ValueError(f"measurement window {label} must include a timezone")  # noqa: TRY003  # tracked: #288
    return timestamp


def _validate_top(top: int) -> None:
    if top <= 0:
        raise ValueError("top must be positive")  # noqa: TRY003  # tracked: #288


def _compare_rows(
    before_rows: list[LatencyRow], after_rows: list[LatencyRow], top: int
) -> list[RowChange]:
    before_by_name = {row.name: row for row in before_rows}
    after_by_name = {row.name: row for row in after_rows}
    # Report rows present in only one side too. Both reports carry only the
    # producer's top-N rows, so a row that entered or left the ranking is
    # exactly a large change; matching on the name intersection alone would
    # silently drop the biggest regressions and improvements.
    names = [row.name for row in after_rows]
    names += [row.name for row in before_rows if row.name not in after_by_name]
    changes: list[RowChange] = []
    for name in names:
        previous = before_by_name.get(name)
        current = after_by_name.get(name)
        before_p95 = previous.p95_ms if previous is not None else None
        after_p95 = current.p95_ms if current is not None else None
        delta = None
        delta_percent = None
        if before_p95 is not None and after_p95 is not None:
            delta = after_p95 - before_p95
            delta_percent = delta / before_p95 * 100 if before_p95 > 0 else None
        changes.append(
            RowChange(
                name=name,
                before_p95_ms=before_p95,
                after_p95_ms=after_p95,
                delta_p95_ms=delta,
                delta_percent=delta_percent,
            )
        )
    changes.sort(key=_change_magnitude, reverse=True)
    return changes[:top]


def _change_magnitude(change: RowChange) -> float:
    # Rank matched rows by absolute p95 change; rank a row present in only one
    # report by its known p95 so a newly hot or newly absent span still surfaces.
    if change.delta_p95_ms is not None:
        return abs(change.delta_p95_ms)
    for value in (change.after_p95_ms, change.before_p95_ms):
        if value is not None:
            return value
    return 0.0


def find_reports(root: str = ".") -> list[str]:  # noqa: D103  # tracked: #288
    return _find_artifacts(root, schema_version=1, model=TelemetryReport)


def find_trace_graphs(root: str = ".") -> list[str]:
    """Find strictly valid servicebench schema-v2 trace graphs."""
    return _find_artifacts(root, schema_version=2, model=TraceGraphReport)


def _find_artifacts(root: str, *, schema_version: int, model: type[BaseModel]) -> list[str]:
    artifacts = []
    for path in Path(root).rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, RecursionError, ValueError):
            # A candidate controls workspace files. One unreadable, malformed,
            # deeply nested, or oversized JSON artifact must not abort discovery.
            continue
        if not isinstance(payload, dict) or payload.get("schema_version") != schema_version:
            continue
        try:
            model.model_validate(payload)
        except ValueError:
            continue
        artifacts.append(path.as_posix())
    return sorted(artifacts)


def build_server() -> FastMCP:  # noqa: D103  # tracked: #288
    mcp = FastMCP("vibesys-otel-profiler")

    @mcp.tool()
    def reports(root: str = ".") -> list[str]:
        """Find normalized servicebench OTel reports below a workspace path."""
        return find_reports(root)

    @mcp.tool()
    def summary(path: str, top: int = 10) -> ReportSummary:
        """Return ranked service, span, and datastore latency evidence."""
        return summarize_report(path, top=top)

    @mcp.tool()
    def compare(before_path: str, after_path: str, top: int = 10) -> ReportComparison:
        """Compare p95 latency for services, spans, and datastores."""
        return compare_reports(before_path, after_path, top=top)

    @mcp.tool()
    def trace_graphs(root: str = ".") -> list[str]:
        """Find versioned servicebench trace graphs below a workspace path."""
        return find_trace_graphs(root)

    @mcp.tool()
    def critical_path(path: str, telemetry_path: str, top: int = 10) -> CriticalPathEvidence:
        """Return a graph's critical path after binding it to normalized telemetry."""
        return summarize_critical_path(path, telemetry_path, top=top)

    return mcp


if __name__ == "__main__":
    build_server().run(transport="stdio")
