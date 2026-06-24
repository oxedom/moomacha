import asyncio
from datetime import datetime, timezone

from control_plane.observability.events import AgentEvent
from control_plane.observability.live_bus import LiveBus
from control_plane.routes.observability import _sse, build_observability_router


def _ev(seq):
    return AgentEvent(type="tool.call", trace_id="tr", turn_id="tn", seq=seq,
                      ts=datetime.now(timezone.utc), attrs={"name": "search"})


def _txt(parts):
    return "".join(p if isinstance(p, str) else p.decode() for p in parts)


def test_sse_formats_event():
    line = _sse(_ev(0))
    assert line.startswith("event: tool.call\n")
    assert '"name": "search"' in line
    assert line.endswith("\n\n")


async def test_live_route_streams_published_event():
    bus = LiveBus()
    router = build_observability_router(bus)
    route = next(r for r in router.routes if r.path == "/observability/live")
    resp = await route.endpoint(turn_id=None)  # StreamingResponse
    assert resp.media_type == "text/event-stream"

    async def collect():
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(chunk)
            if "search" in _txt(parts):
                return _txt(parts)
        return ""

    async def publish_soon():
        await asyncio.sleep(0.05)
        bus.publish(_ev(0))

    pub = asyncio.create_task(publish_soon())
    out = await asyncio.wait_for(collect(), timeout=5.0)
    pub.cancel()
    assert "event: tool.call" in out and "search" in out
    assert ": connected" in out  # the stream opens immediately
