"""Tests for the shared supervision diagnostic contract."""

from vibesys.server.diagnostics import (
    Diagnostic,
    DiagnosticRetryability,
    DiagnosticScope,
    DiagnosticSeverity,
    exception_detail,
    exception_to_diagnostic,
    redact_diagnostic_text,
)
from vibesys.server.events import EventType, RunEvent, make_event
from vibesys.server.protocol import ProtocolErrorMessage, Response


def test_diagnostic_round_trips_on_protocol_and_event_models() -> None:
    diagnostic = Diagnostic(
        code="permission_denied",
        summary="The operation was denied",
        detail="PermissionError: sandbox rejected the operation",
        scope=DiagnosticScope.INVOCATION,
        severity=DiagnosticSeverity.FATAL,
        retryability=DiagnosticRetryability.NEVER,
        hint="Check the sandbox permissions.",
        debug_ref="run-events.jsonl:12",
    )

    response = Response(
        request_id="request", ok=False, error=diagnostic.summary, diagnostic=diagnostic
    )
    restored_response = Response.model_validate_json(response.model_dump_json())
    assert restored_response.diagnostic == diagnostic

    protocol_error = ProtocolErrorMessage(
        code="request_failed", message=diagnostic.summary, diagnostic=diagnostic
    )
    assert (
        ProtocolErrorMessage.model_validate_json(protocol_error.model_dump_json()).diagnostic
        == diagnostic
    )

    event = make_event(EventType.RUN_FAILED, diagnostic.summary, diagnostic=diagnostic)
    restored_event = RunEvent.model_validate_json(event.model_dump_json())
    assert restored_event.diagnostic == diagnostic


def test_legacy_payloads_without_diagnostic_still_parse() -> None:
    response = Response.model_validate_json('{"request_id":"request","ok":false,"error":"failed"}')
    assert response.diagnostic is None
    event = RunEvent.model_validate_json(
        '{"protocol_version":1,"timestamp":"2026-01-01T00:00:00Z","type":"run_failed","text":"failed"}'
    )
    assert event.diagnostic is None


def test_exception_conversion_maps_type_and_redacts_credentials() -> None:
    diagnostic = exception_to_diagnostic(
        PermissionError("token=abc123 Bearer secret-value"),
        scope=DiagnosticScope.TRANSPORT,
        operation="Codex startup",
        retryability=DiagnosticRetryability.MANUAL,
    )
    assert diagnostic.code == "permission_denied"
    assert diagnostic.summary == "Codex startup was denied"
    assert diagnostic.detail == "PermissionError: token=[REDACTED] Bearer [REDACTED]"
    assert diagnostic.summary != diagnostic.detail
    assert diagnostic.scope is DiagnosticScope.TRANSPORT
    assert diagnostic.severity is DiagnosticSeverity.ERROR
    assert diagnostic.retryability is DiagnosticRetryability.MANUAL


def test_exception_conversion_classifies_wrapped_known_exception_and_keeps_chain() -> None:
    cause = PermissionError("OPENAI_API_KEY=secret")
    wrapped = RuntimeError("agent startup failed")
    wrapped.__cause__ = cause

    diagnostic = exception_to_diagnostic(
        wrapped, scope=DiagnosticScope.INVOCATION, operation="Agent startup"
    )
    assert diagnostic.code == "permission_denied"
    assert diagnostic.summary == "Agent startup was denied"
    assert diagnostic.detail == (
        "RuntimeError: agent startup failed <- PermissionError: OPENAI_API_KEY=[REDACTED]"
    )


def test_exception_conversion_uses_unsuppressed_context_and_bounds_cycles() -> None:
    outer = RuntimeError("outer")
    inner = TimeoutError("inner")
    outer.__context__ = inner
    inner.__context__ = outer
    diagnostic = exception_to_diagnostic(outer, scope=DiagnosticScope.RUN, operation="Run")
    assert diagnostic.code == "timeout"
    assert diagnostic.detail == "RuntimeError: outer <- TimeoutError: inner"


def test_exception_conversion_maps_known_exception_subclasses() -> None:
    class SandboxPermissionError(PermissionError):
        pass

    diagnostic = exception_to_diagnostic(
        SandboxPermissionError("denied"),
        scope=DiagnosticScope.INVOCATION,
        operation="Sandbox setup",
    )
    assert diagnostic.code == "permission_denied"
    assert diagnostic.summary == "Sandbox setup was denied"


def test_diagnostic_redacts_explicit_text_on_construction_and_round_trip() -> None:
    diagnostic = Diagnostic(
        code="provider_failed",
        summary="OPENAI_API_KEY=summary-secret",
        detail="AWS_SECRET_ACCESS_KEY=detail-secret",
        hint="Use FOO_TOKEN=hint-secret",
        scope=DiagnosticScope.REQUEST,
    )
    assert diagnostic.summary == "OPENAI_API_KEY=[REDACTED]"
    assert diagnostic.detail == "AWS_SECRET_ACCESS_KEY=[REDACTED]"
    assert diagnostic.hint == "Use FOO_TOKEN=[REDACTED]"

    restored = Diagnostic.model_validate_json(diagnostic.model_dump_json())
    assert restored == diagnostic


def test_empty_exception_uses_exception_type_as_user_message() -> None:
    assert exception_detail(RuntimeError()) == "RuntimeError"
    assert (
        exception_to_diagnostic(TimeoutError(), scope=DiagnosticScope.RUN, operation="Run").summary
        == "Run timed out"
    )
    assert redact_diagnostic_text("password: hidden") == "password: [REDACTED]"
