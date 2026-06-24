from datetime import datetime, timezone

from control_plane.observability.events import AgentEvent
from control_plane.observability.sink import MultiSink, NullSink


def _ev() -> AgentEvent:
    return AgentEvent(type="x", trace_id="t", turn_id="u", seq=0,
                      ts=datetime.now(timezone.utc), attrs={})


async def test_multisink_fans_out_to_all():
    a, b = [], []

    async def sink_a(ev): a.append(ev)
    async def sink_b(ev): b.append(ev)

    ms = MultiSink([sink_a, sink_b])
    await ms.emit(_ev())
    assert len(a) == 1 and len(b) == 1


async def test_multisink_swallows_a_failing_sink():
    good = []

    async def boom(ev): raise RuntimeError("sink down")
    async def good_sink(ev): good.append(ev)

    ms = MultiSink([boom, good_sink])
    await ms.emit(_ev())  # must NOT raise
    assert len(good) == 1  # the healthy sink still received it


async def test_nullsink_is_noop():
    await NullSink().emit(_ev())  # no error
