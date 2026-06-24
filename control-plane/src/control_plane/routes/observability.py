from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from control_plane.observability.events import AgentEvent
from control_plane.observability.live_bus import LiveBus


def _sse(event: AgentEvent) -> str:
    data = {
        "type": event.type, "trace_id": event.trace_id, "turn_id": event.turn_id,
        "seq": event.seq, "ts": event.ts.isoformat(), "attrs": event.attrs,
    }
    return f"event: {event.type}\ndata: {json.dumps(data, default=str)}\n\n"


def build_observability_router(bus: LiveBus) -> APIRouter:
    router = APIRouter(prefix="/observability", tags=["observability"])

    @router.get("/live")
    async def live(turn_id: str | None = Query(default=None)) -> StreamingResponse:
        async def gen() -> AsyncIterator[str]:
            with bus.subscribe(turn_id=turn_id) as q:
                yield ": connected\n\n"  # open the stream immediately
                while True:
                    event = await q.get()
                    yield _sse(event)

        return StreamingResponse(gen(), media_type="text/event-stream")

    return router
