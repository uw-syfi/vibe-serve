"""Process-global publisher for presentation-neutral core events.

Producers publish unconditionally. Application entrypoints compose subscribers,
such as the headless renderer, durable core journal, or server adapter.
This module has no knowledge of any serving implementation.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from vibesys.run.events import (
    AgentOutputChannel,
    AgentOutputChunkData,
    AgentStatusData,
    CoreEvent,
    CoreEventData,
    CoreEventType,
    EventStatus,
    JsonResultPayload,
    TodoItemData,
    TodoUpdateData,
    ToolCallData,
    ToolResultData,
    ToolResultPayload,
    UsageUpdateData,
    make_core_event,
)

EventHandler = Callable[[CoreEvent], object]


def _json_safe(args: dict[str, Any]) -> dict[str, Any]:
    """Coerce tool arguments to a JSON-serializable dictionary."""
    return json.loads(json.dumps(args, default=repr))


def _classify_tool_result(content: str) -> ToolResultPayload | None:
    """Preserve JSON-shaped tool results alongside their raw text."""
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(value, (dict, list)):
        return JsonResultPayload(value=value)
    return None


class OutputSink:
    """Fan core events out to explicitly composed in-process subscribers."""

    def __init__(self) -> None:  # noqa: D107  # tracked: #288
        self._lock = threading.Lock()
        self._subscribers: tuple[EventHandler, ...] = ()

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        """Register ``handler`` and return an idempotent unsubscriber."""
        with self._lock:
            self._subscribers = (*self._subscribers, handler)

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers = tuple(h for h in self._subscribers if h is not handler)

        return unsubscribe

    def emit(
        self,
        event_type: CoreEventType,
        text: str = "",
        *,
        data: CoreEventData | None = None,
        status: EventStatus | None = None,
        agent_kind: str | None = None,
        round_label: str | None = None,
        execution_id: str | None = None,
    ) -> CoreEvent:
        """Publish one typed core event and return the emitted value."""
        event = make_core_event(
            event_type,
            text,
            data=data,
            status=status,
            agent_kind=agent_kind,
            round_label=round_label,
            execution_id=execution_id,
        )
        with self._lock:
            subscribers = self._subscribers
        for handler in subscribers:
            handler(event)
        return event

    def agent_output(  # noqa: D102  # tracked: #288
        self,
        content: str,
        *,
        channel: AgentOutputChannel = "assistant",
        status: AgentStatusData | None = None,
        agent_kind: str | None = None,
        round_label: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        if not content:
            return
        self.emit(
            CoreEventType.AGENT_OUTPUT_CHUNK,
            data=AgentOutputChunkData(channel=channel, content=content, status=status),
            agent_kind=agent_kind,
            round_label=round_label,
            execution_id=invocation_id,
        )

    def tool_call(  # noqa: D102  # tracked: #288
        self,
        tool: str,
        args: dict[str, Any],
        *,
        call_id: str | None = None,
        status: AgentStatusData | None = None,
        agent_kind: str | None = None,
        round_label: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        self.emit(
            CoreEventType.TOOL_CALL,
            data=ToolCallData(tool=tool, call_id=call_id, args=_json_safe(args), status=status),
            agent_kind=agent_kind,
            round_label=round_label,
            execution_id=invocation_id,
        )

    def tool_result(  # noqa: D102  # tracked: #288
        self,
        tool: str,
        content: str,
        *,
        call_id: str | None = None,
        is_error: bool = False,
        payload: ToolResultPayload | None = None,
        agent_kind: str | None = None,
        round_label: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        if payload is None:
            payload = _classify_tool_result(content)
        self.emit(
            CoreEventType.TOOL_RESULT,
            data=ToolResultData(
                tool=tool,
                call_id=call_id,
                content=content,
                is_error=is_error,
                payload=payload,
            ),
            agent_kind=agent_kind,
            round_label=round_label,
            execution_id=invocation_id,
        )

    def todo_update(  # noqa: D102  # tracked: #288
        self,
        todos: list[TodoItemData],
        *,
        agent_kind: str | None = None,
        round_label: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        if not todos:
            return
        self.emit(
            CoreEventType.TODO_UPDATE,
            data=TodoUpdateData(todos=todos),
            agent_kind=agent_kind,
            round_label=round_label,
            execution_id=invocation_id,
        )

    def usage_update(  # noqa: D102  # tracked: #288
        self,
        input_tokens: int,
        *,
        context_window: int | None = None,
        model: str | None = None,
        agent_kind: str | None = None,
        round_label: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        self.emit(
            CoreEventType.USAGE_UPDATE,
            data=UsageUpdateData(
                input_tokens=input_tokens,
                context_window=context_window,
                model=model,
            ),
            agent_kind=agent_kind,
            round_label=round_label,
            execution_id=invocation_id,
        )


_SINK = OutputSink()


def output_sink() -> OutputSink:
    """Return the process-global core event publisher."""
    return _SINK
