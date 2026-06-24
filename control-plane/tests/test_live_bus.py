import asyncio
from datetime import datetime, timezone

from control_plane.observability.events import AgentEvent
from control_plane.observability.live_bus import LiveBus, LiveSink


def _ev(turn_id, seq):
    return AgentEvent(type="tool.call", trace_id="tr", turn_id=turn_id, seq=seq,
                      ts=datetime.now(timezone.utc), attrs={"name": "x"})


async def test_subscriber_receives_published_events():
    bus = LiveBus()
    sink = LiveSink(bus)
    with bus.subscribe() as q:
        await sink.emit(_ev("tn", 0))
        ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.turn_id == "tn" and ev.seq == 0


async def test_no_subscriber_is_noop():
    bus = LiveBus()
    await LiveSink(bus).emit(_ev("tn", 0))  # must not raise or block


async def test_turn_filter_only_gets_its_turn():
    bus = LiveBus()
    sink = LiveSink(bus)
    with bus.subscribe(turn_id="A") as q:
        await sink.emit(_ev("B", 0))
        await sink.emit(_ev("A", 1))
        ev = await asyncio.wait_for(q.get(), timeout=1.0)
    assert ev.turn_id == "A"
