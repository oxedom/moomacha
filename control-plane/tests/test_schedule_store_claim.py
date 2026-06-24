import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import ScheduledJobRow
from control_plane.services.schedule_store import ScheduleStore

GRACE = 3600


async def _store():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    events = []

    async def fake_write_event(**kwargs):
        events.append(kwargs)

    return ScheduleStore(factory, fake_write_event), factory, engine, events


async def _get(factory, sid):
    async with factory() as session:
        return (await session.execute(
            select(ScheduledJobRow).where(ScheduledJobRow.id == sid)
        )).scalar_one()


async def test_due_one_shot_within_grace_fires_and_completes():
    store, factory, engine, events = await _store()
    try:
        now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        row = await store.create_one_shot(agent_id=uuid.uuid4(), channel="c", topic="t",
                                          instruction="go", run_at=now - timedelta(minutes=1))
        outcomes = await store.claim_due(now, GRACE, limit=100)
        assert len(outcomes) == 1
        assert outcomes[0].action == "fire"
        assert outcomes[0].scheduled_for == row.next_run_at
        assert (await _get(factory, row.id)).status == "completed"
        assert (await _get(factory, row.id)).last_run_at == now
    finally:
        await engine.dispose()


async def test_one_shot_past_grace_is_missed_not_fired():
    store, factory, engine, events = await _store()
    try:
        now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        row = await store.create_one_shot(agent_id=uuid.uuid4(), channel="c", topic="t",
                                          instruction="go", run_at=now - timedelta(hours=5))
        outcomes = await store.claim_due(now, GRACE, limit=100)
        assert [o.action for o in outcomes] == ["missed"]
        assert (await _get(factory, row.id)).status == "missed"
        assert any(e["event_type"] == "schedule_missed" for e in events)
    finally:
        await engine.dispose()


async def test_recurring_within_grace_fires_and_advances_next_run():
    store, factory, engine, events = await _store()
    try:
        now = datetime(2026, 5, 24, 12, 0, 30, tzinfo=UTC)
        row = await store.create_recurring(agent_id=uuid.uuid4(), channel="c", topic="t",
                                           instruction="tick", cron="0 * * * *",
                                           timezone="UTC", now=now)
        # next_run_at is 13:00; force it due by rewinding to the 12:00 occurrence.
        async with factory() as session:
            r = await session.get(ScheduledJobRow, row.id)
            r.next_run_at = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
            await session.commit()
        outcomes = await store.claim_due(now, GRACE, limit=100)
        assert [o.action for o in outcomes] == ["fire"]
        assert outcomes[0].scheduled_for == datetime(2026, 5, 24, 12, 0, tzinfo=UTC)  # original occurrence, not advanced
        after = await _get(factory, row.id)
        assert after.status == "active"
        assert after.next_run_at == datetime(2026, 5, 24, 13, 0, tzinfo=UTC)  # after now
        assert after.last_run_at == now
    finally:
        await engine.dispose()


async def test_recurring_past_grace_rolls_forward_without_firing():
    store, factory, engine, events = await _store()
    try:
        now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        row = await store.create_recurring(agent_id=uuid.uuid4(), channel="c", topic="t",
                                           instruction="tick", cron="0 * * * *",
                                           timezone="UTC", now=now)
        async with factory() as session:
            r = await session.get(ScheduledJobRow, row.id)
            r.next_run_at = datetime(2026, 5, 24, 6, 0, tzinfo=UTC)  # 6h stale
            await session.commit()
        outcomes = await store.claim_due(now, GRACE, limit=100)
        assert outcomes == []  # nothing fired or missed
        after = await _get(factory, row.id)
        assert after.status == "active"
        assert after.next_run_at == datetime(2026, 5, 24, 13, 0, tzinfo=UTC)  # rolled past now
        assert not any(e["event_type"] == "schedule_missed" for e in events)  # recurring never "missed"
    finally:
        await engine.dispose()


async def test_not_yet_due_rows_are_not_claimed():
    store, factory, engine, events = await _store()
    try:
        now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        await store.create_one_shot(agent_id=uuid.uuid4(), channel="c", topic="t",
                                    instruction="later", run_at=now + timedelta(hours=1))
        assert await store.claim_due(now, GRACE, limit=100) == []
    finally:
        await engine.dispose()


async def test_limit_caps_rows_per_tick():
    store, factory, engine, events = await _store()
    try:
        now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        for i in range(3):
            await store.create_one_shot(agent_id=uuid.uuid4(), channel="c", topic=f"t{i}",
                                        instruction="go", run_at=now - timedelta(minutes=1))
        outcomes = await store.claim_due(now, GRACE, limit=2)
        assert len(outcomes) == 2
    finally:
        await engine.dispose()


async def test_malformed_recurring_is_quarantined_without_blocking_batch():
    store, factory, engine, events = await _store()
    try:
        now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        good = await store.create_one_shot(agent_id=uuid.uuid4(), channel="c", topic="good",
                                           instruction="go", run_at=now - timedelta(minutes=1))
        async with factory() as session:
            bad = ScheduledJobRow(
                agent_id=uuid.uuid4(), channel="c", topic="bad", kind="recurring",
                cron_expression=None, timezone="UTC", instruction="broken",
                status="active", next_run_at=now - timedelta(minutes=1),
            )
            session.add(bad)
            await session.commit()
            bad_id = bad.id

        outcomes = await store.claim_due(now, GRACE, limit=100)  # must NOT raise

        assert any(o.action == "fire" and o.topic == "good" for o in outcomes)  # good row still fired
        assert (await _get(factory, bad_id)).status == "error"  # bad row quarantined
        assert (await _get(factory, good.id)).status == "completed"
        assert any(e["event_type"] == "schedule_errored" for e in events)
    finally:
        await engine.dispose()
