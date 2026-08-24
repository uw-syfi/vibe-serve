"""Local JSONL transport for presentation clients."""

from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
import time
from contextlib import suppress
from pathlib import Path  # noqa: TC003  # tracked: #288

from pydantic import BaseModel, TypeAdapter

from vibesys.server.diagnostics import DiagnosticScope, exception_to_diagnostic
from vibesys.server.protocol import (
    EventBatchMessage,
    EventMessage,
    ProtocolErrorMessage,
    ProtocolRequest,
    Response,
    SubscribedMessage,
    SubscribeRequest,
)
from vibesys.server.service import SupervisionService  # noqa: TC001  # tracked: #288

_REQUEST_ADAPTER = TypeAdapter(ProtocolRequest)


class _RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        service: SupervisionService = self.server.service  # type: ignore[attr-defined]
        for line in self.rfile:
            request_id = "unknown"
            try:
                raw = json.loads(line)
                request_id = str(raw.get("request_id", request_id))
                request = _REQUEST_ADAPTER.validate_python(raw)
                if isinstance(request, SubscribeRequest):
                    self.server.client_subscribed.set()  # type: ignore[attr-defined]
                    try:
                        self._stream(request)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    except Exception as exc:  # noqa: BLE001  # tracked: #288
                        self._write_stream_error(request.request_id, exc)
                    finally:
                        self.server.client_disconnected.set()  # type: ignore[attr-defined]
                    return
                response = service.execute(request)
            except Exception as exc:  # noqa: BLE001  # tracked: #288
                diagnostic = exception_to_diagnostic(
                    exc,
                    scope=DiagnosticScope.REQUEST,
                    operation="Request",
                )
                response = Response(
                    request_id=request_id,
                    ok=False,
                    error=diagnostic.summary,
                    diagnostic=diagnostic,
                )
            self.wfile.write(response.model_dump_json().encode() + b"\n")
            self.wfile.flush()

    def _stream(self, request: SubscribeRequest) -> None:
        service: SupervisionService = self.server.service  # type: ignore[attr-defined]
        snapshot = service.snapshot()
        self._write_message(
            SubscribedMessage(
                request_id=request.request_id,
                run_id=snapshot.run_id,
                latest_sequence=snapshot.sequence,
            )
        )
        cursor = request.after_sequence
        replay = service.events(cursor)
        if replay:
            self._write_message(EventBatchMessage(events=replay))
            cursor = max(event.sequence for event in replay)
        while True:
            events = service.wait_for_events(cursor, timeout=1.0)
            if not events:
                if self._client_disconnected():
                    return
                time.sleep(0.05)
                continue
            for event in events:
                self._write_message(EventMessage(event=event))
                cursor = event.sequence

    def _write_stream_error(self, request_id: str, error: Exception) -> None:
        """Report a replay or stream failure without hiding a live connection."""
        diagnostic = exception_to_diagnostic(
            error,
            scope=DiagnosticScope.PROTOCOL,
            operation="Event stream",
            code="stream_failed",
        )
        with suppress(BrokenPipeError, ConnectionResetError):
            self._write_message(
                ProtocolErrorMessage(
                    request_id=request_id,
                    code=diagnostic.code,
                    message=diagnostic.summary,
                    diagnostic=diagnostic,
                )
            )

    def _client_disconnected(self) -> bool:
        try:
            return self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
        except BlockingIOError:
            return False
        except OSError:
            return True

    def _write_message(self, message: BaseModel) -> None:
        payload = message.model_dump_json()
        self.wfile.write(payload.encode() + b"\n")
        self.wfile.flush()


class _UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: Path, service: SupervisionService):  # noqa: ANN204  # tracked: #288
        self.service = service
        super().__init__(str(path), _RequestHandler)


class SupervisionSocketServer:
    """Own a private Unix socket serving one or more concurrent clients."""

    def __init__(self, path: Path, service: SupervisionService):  # noqa: ANN204, D107  # tracked: #288
        self.path = path
        self.service = service
        self._server: _UnixServer | None = None
        self._thread: threading.Thread | None = None
        self._client_subscribed = threading.Event()
        self._client_disconnected = threading.Event()

    def start(self) -> None:  # noqa: D102  # tracked: #288
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        self._server = _UnixServer(self.path, self.service)
        self._server.client_subscribed = self._client_subscribed  # type: ignore[attr-defined]
        self._server.client_disconnected = self._client_disconnected  # type: ignore[attr-defined]
        os.chmod(self.path, 0o600)  # noqa: PTH101  # tracked: #288
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vibesys-supervision-server",
            daemon=True,
        )
        self._thread.start()

    def wait_for_subscriber(self, timeout: float) -> bool:
        """Wait until the presentation client has established its event stream."""
        return self._client_subscribed.wait(timeout)

    def wait_for_subscriber_disconnect(self) -> None:
        """Keep terminal events queryable until the attached client exits."""
        self._client_disconnected.wait()

    def close(self) -> None:  # noqa: D102  # tracked: #288
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.path.unlink(missing_ok=True)

    def __enter__(self) -> SupervisionSocketServer:  # noqa: D105  # tracked: #288
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:  # noqa: D105  # tracked: #288
        self.close()
