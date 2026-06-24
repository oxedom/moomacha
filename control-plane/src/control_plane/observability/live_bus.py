from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator

from control_plane.observability.events import AgentEvent


class LiveBus:
    """In-process fan-out for live event tails. Each subscriber gets its own bounded
    queue; a slow/full subscriber drops events (we favor liveness over completeness —
    the durable record lives in the audit log)."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._subs: set[tuple[str | None, asyncio.Queue[AgentEvent]]] = set()
        self._maxsize = maxsize

    @contextlib.contextmanager
    def subscribe(self, turn_id: str | None = None) -> Iterator[asyncio.Queue[AgentEvent]]:
        q: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=self._maxsize)
        entry = (turn_id, q)
        self._subs.add(entry)
        try:
            yield q
        finally:
            self._subs.discard(entry)

    def publish(self, event: AgentEvent) -> None:
        for turn_id, q in self._subs:
            if turn_id is not None and turn_id != event.turn_id:
                continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop for slow subscriber


class LiveSink:
    """EventSink that publishes to a LiveBus (fire-and-forget; never blocks)."""

    def __init__(self, bus: LiveBus) -> None:
        self._bus = bus

    async def emit(self, event: AgentEvent) -> None:
        self._bus.publish(event)
