import uuid
from datetime import UTC, datetime

from control_plane.services.job_source import ScheduleSource
from control_plane.services.schedule_store import ClaimOutcome
from control_plane.services.scheduler import SchedulerDeps, SchedulerLoop


class FakeStore:
    def __init__(self, outcomes):
        self._outcomes = outcomes
        self.claim_calls = []

    async def claim_due(self, now, grace_seconds, limit):
        self.claim_calls.append((now, grace_seconds, limit))
        return self._outcomes


async def test_tick_fires_each_fire_outcome_through_enqueue_turn():
    sid, aid = uuid.uuid4(), uuid.uuid4()
    when = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    store = FakeStore([
        ClaimOutcome(sid, aid, "c", "t", "do it", when, "fire"),
        ClaimOutcome(uuid.uuid4(), aid, "c", "t2", "skip", when, "missed"),
    ])
    fired = []

    async def enqueue_turn(*, agent_id, channel, topic, content, source):
        fired.append((agent_id, channel, topic, content, source))

    deps = SchedulerDeps(
        store=store, enqueue_turn=enqueue_turn,
        clock=lambda: datetime(2026, 5, 24, 12, 0, 5, tzinfo=UTC),
        grace_seconds=3600, max_due_per_tick=50,
    )
    await SchedulerLoop(deps).tick()

    assert store.claim_calls == [(datetime(2026, 5, 24, 12, 0, 5, tzinfo=UTC), 3600, 50)]
    assert len(fired) == 1  # only the "fire" outcome
    agent_id, channel, topic, content, source = fired[0]
    assert (agent_id, channel, topic, content) == (aid, "c", "t", "do it")
    assert isinstance(source, ScheduleSource)
    assert source.schedule_id == sid
    assert source.scheduled_for == when


async def test_tick_survives_an_enqueue_error_for_other_rows():
    aid = uuid.uuid4()
    when = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    store = FakeStore([
        ClaimOutcome(uuid.uuid4(), aid, "c", "t1", "boom", when, "fire"),
        ClaimOutcome(uuid.uuid4(), aid, "c", "t2", "ok", when, "fire"),
    ])
    fired = []

    async def enqueue_turn(*, agent_id, channel, topic, content, source):
        if topic == "t1":
            raise RuntimeError("queue down")
        fired.append(topic)

    deps = SchedulerDeps(store=store, enqueue_turn=enqueue_turn,
                         clock=lambda: when, grace_seconds=3600, max_due_per_tick=50)
    await SchedulerLoop(deps).tick()  # must not raise
    assert fired == ["t2"]


async def test_tick_with_no_due_rows_is_a_noop():
    store = FakeStore([])

    async def enqueue_turn(*, agent_id, channel, topic, content, source):
        raise AssertionError("must not fire when there are no due rows")

    deps = SchedulerDeps(
        store=store, enqueue_turn=enqueue_turn,
        clock=lambda: datetime(2026, 5, 24, 12, 0, tzinfo=UTC),
        grace_seconds=10, max_due_per_tick=5,
    )
    await SchedulerLoop(deps).tick()  # must not raise
    assert len(store.claim_calls) == 1  # claim_due was still called


async def test_run_forever_ticks_repeatedly_then_stops_on_cancel():
    import asyncio

    store = FakeStore([])

    async def enqueue_turn(*, agent_id, channel, topic, content, source):
        return None

    deps = SchedulerDeps(
        store=store, enqueue_turn=enqueue_turn,
        clock=lambda: datetime(2026, 5, 24, 12, 0, tzinfo=UTC),
        grace_seconds=10, max_due_per_tick=5,
    )
    task = asyncio.create_task(SchedulerLoop(deps).run_forever(0))
    while len(store.claim_calls) < 2:
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(store.claim_calls) >= 2  # the loop kept ticking until cancelled
