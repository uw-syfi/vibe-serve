"""Headless supervision server runtime."""

from __future__ import annotations

import signal
from collections.abc import Callable  # noqa: TC003  # tracked: #288
from pathlib import Path  # noqa: TC003  # tracked: #288
from typing import Any

from vibesys.errors import ConfigurationError
from vibesys.server.diagnostics import (
    Diagnostic,
    DiagnosticRetryability,
    DiagnosticScope,
    DiagnosticSeverity,
    exception_to_diagnostic,
)
from vibesys.server.events import (
    ConfigurationFailedData,
    EventStatus,
    EventType,
    RunInterruptedData,
    ServerReadyData,
)
from vibesys.server.registry import REGISTRY
from vibesys.server.service import SupervisionService
from vibesys.server.supervisor import RunSupervisor
from vibesys.server.transport import SupervisionSocketServer


def run_server(
    run: Callable[[], Any],
    *,
    socket_path: Path,
) -> Any:  # noqa: ANN401  # tracked: #288
    """Run a headless backend that exposes supervision over a Unix socket."""
    supervisor = RunSupervisor()
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def interrupt_from_launcher(signum: int, frame: object) -> None:
        del signum, frame
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt_from_launcher)
    service = SupervisionService(supervisor)
    REGISTRY.activate(supervisor)
    supervisor.attach(socket_path.parent)
    supervisor.record(
        EventType.SERVER_READY,
        status=EventStatus.ACTIVE,
        data=ServerReadyData(),
    )
    try:
        with SupervisionSocketServer(socket_path, service) as server:
            if not server.wait_for_subscriber(timeout=30.0):
                raise RuntimeError("Timed out waiting for a supervision client")  # noqa: TRY003  # tracked: #288
            try:
                value = run()
            except KeyboardInterrupt:
                launcher_error = RuntimeError("launcher_terminated (SIGTERM)")
                event_diagnostic = exception_to_diagnostic(
                    launcher_error,
                    scope=DiagnosticScope.RUN,
                    operation="Run",
                    summary="Run interrupted",
                    code="interrupted",
                    severity=DiagnosticSeverity.FATAL,
                    retryability=DiagnosticRetryability.NEVER,
                )
                supervisor.record(
                    EventType.RUN_INTERRUPTED,
                    status=EventStatus.FAILED,
                    data=RunInterruptedData(reason="launcher_terminated", signal="SIGTERM"),
                    diagnostic=event_diagnostic,
                )
                supervisor.finish(launcher_error, diagnostic=event_diagnostic)
                raise
            except ConfigurationError as exc:
                configuration_diagnostic = exc.diagnostic
                event_diagnostic = Diagnostic(
                    code=configuration_diagnostic.code,
                    summary=configuration_diagnostic.message,
                    detail=(
                        f"Stage: {configuration_diagnostic.stage}\n"
                        f"Exit code: {configuration_diagnostic.exit_code}"
                    ),
                    hint=configuration_diagnostic.usage,
                    scope=DiagnosticScope.CONFIGURATION,
                    severity=DiagnosticSeverity.FATAL,
                    retryability=DiagnosticRetryability.NEVER,
                )
                configuration_message = event_diagnostic.summary
                configuration_usage = event_diagnostic.hint
                supervisor.record(
                    EventType.CONFIGURATION_FAILED,
                    configuration_message,
                    status=EventStatus.FAILED,
                    data=ConfigurationFailedData(
                        code=configuration_diagnostic.code,
                        stage=configuration_diagnostic.stage,
                        message=configuration_message,
                        usage=configuration_usage,
                        exit_code=configuration_diagnostic.exit_code,
                    ),
                    diagnostic=event_diagnostic,
                )
                supervisor.finish(exc, record_event=False, diagnostic=event_diagnostic)
                server.wait_for_subscriber_disconnect()
                raise
            except BaseException as exc:
                supervisor.finish(exc)
                server.wait_for_subscriber_disconnect()
                raise
            supervisor.finish()
            server.wait_for_subscriber_disconnect()
            return value
    finally:
        REGISTRY.deactivate(supervisor)
        signal.signal(signal.SIGTERM, previous_sigterm)
