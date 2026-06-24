import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import ScheduledJobRow
from control_plane.services.schedule_store import ScheduleStore


def _now():
    return datetime(2026, 5, 24, 12, 0, tzinfo=UTC)


async def _store():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    events = []

    async def fake_write_event(**kwargs):
        events.append(kwargs)

    return ScheduleStore(factory, fake_write_event), factory, engine, events


async def test_create_one_shot_sets_next_run_at_to_run_at_and_emits_event():
    store, factory, engine, events = await _store()
    try:
        run_at = _now() + timedelta(hours=2)
        row = await store.create_one_shot(
            agent_id=uuid.uuid4(), channel="sandbox", topic="t",
            instruction="ping", run_at=run_at,
        )
        assert row.kind == "one_shot"
        assert row.next_run_at == run_at
        assert row.cron_expression is None
        assert any(e["event_type"] == "schedule_created" for e in events)
    finally:
        await engine.dispose()


async def test_create_recurring_computes_next_cron_after_now():
    store, factory, engine, events = await _store()
    try:
        row = await store.create_recurring(
            agent_id=uuid.uuid4(), channel="sandbox", topic="t",
            instruction="standup", cron="0 9 * * *", timezone="UTC", now=_now(),
        )
        assert row.kind == "recurring"
        # next 09:00 UTC strictly after 2026-05-24 12:00 is 2026-05-25 09:00
        assert row.next_run_at == datetime(2026, 5, 25, 9, 0, tzinfo=UTC)
    finally:
        await engine.dispose()


async def test_list_for_topic_filters_by_channel_topic_and_active():
    store, factory, engine, events = await _store()
    try:
        aid = uuid.uuid4()
        await store.create_one_shot(agent_id=aid, channel="c", topic="t1",
                                    instruction="a", run_at=_now() + timedelta(hours=1))
        await store.create_one_shot(agent_id=aid, channel="c", topic="t2",
                                    instruction="b", run_at=_now() + timedelta(hours=1))
        rows = await store.list_for_topic("c", "t1")
        assert len(rows) == 1
        assert rows[0].instruction == "a"
    finally:
        await engine.dispose()


async def test_cancel_in_topic_succeeds_other_topic_refused():
    store, factory, engine, events = await _store()
    try:
        aid = uuid.uuid4()
        row = await store.create_one_shot(agent_id=aid, channel="c", topic="t1",
                                          instruction="a", run_at=_now() + timedelta(hours=1))
        assert await store.cancel(row.id, "c", "WRONG") is False
        assert await store.cancel(row.id, "c", "t1") is True
        async with factory() as session:
            got = (await session.execute(select(ScheduledJobRow))).scalar_one()
        assert got.status == "cancelled"
        assert any(e["event_type"] == "schedule_cancelled" for e in events)
        assert await store.cancel(uuid.uuid4(), "c", "t1") is False  # missing id
    finally:
        await engine.dispose()


async def test_next_cron_rejects_naive_datetime():
    import pytest

    from control_plane.services.schedule_store import next_cron

    with pytest.raises(ValueError):
        next_cron("0 9 * * *", datetime(2026, 5, 24, 12, 0), "UTC")  # naive
