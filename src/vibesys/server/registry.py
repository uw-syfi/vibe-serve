"""Guarded process-local bridge between TUI runtime and RunContext."""

from __future__ import annotations

import threading

from vibesys.server.supervisor import RunSupervisor  # noqa: TC001  # tracked: #288


class SupervisorRegistry:  # noqa: D101  # tracked: #288
    def __init__(self) -> None:  # noqa: D107  # tracked: #288
        self._lock = threading.Lock()
        self._active: RunSupervisor | None = None

    def activate(self, supervisor: RunSupervisor) -> None:  # noqa: D102  # tracked: #288
        with self._lock:
            if self._active is not None:
                raise RuntimeError("A TUI-supervised run is already active in this process")  # noqa: TRY003  # tracked: #288
            self._active = supervisor

    def get(self) -> RunSupervisor | None:  # noqa: D102  # tracked: #288
        with self._lock:
            return self._active

    def deactivate(self, supervisor: RunSupervisor) -> None:  # noqa: D102  # tracked: #288
        with self._lock:
            if self._active is supervisor:
                self._active = None


REGISTRY = SupervisorRegistry()


def active_supervisor() -> RunSupervisor | None:  # noqa: D103  # tracked: #288
    return REGISTRY.get()
