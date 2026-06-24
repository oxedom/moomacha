from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from control_plane.observability.events import AgentEvent

logger = logging.getLogger("control_plane")

Sink = Callable[[AgentEvent], Awaitable[None]]


@runtime_checkable
class EventSink(Protocol):
    async def emit(self, event: AgentEvent) -> None: ...


class NullSink:
    """A sink that drops everything (default when observability is off)."""

    async def emit(self, event: AgentEvent) -> None:
        return None


class MultiSink:
    """Fan one event out to many sinks. A failing sink is logged and swallowed so
    telemetry can never break a turn (the audit sink is written transactionally
    elsewhere, so completeness does not depend on this best-effort fan-out)."""

    def __init__(self, sinks: list[Sink]) -> None:
        self._sinks = sinks

    async def emit(self, event: AgentEvent) -> None:
        for sink in self._sinks:
            try:
                await sink(event)
            except Exception:  # noqa: BLE001 - a sink must never kill the turn
                logger.exception("event sink failed for %s", event.type)
