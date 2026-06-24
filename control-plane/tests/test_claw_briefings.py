import uuid
from datetime import UTC, datetime

from control_plane.db.engine import build_session_factory, create_all
from control_plane.services.claw_briefings import BRIEFINGS, TIMEZONE, seed_claw_briefings
from control_plane.services.schedule_store import ScheduleStore


def _now():
    return datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


async def _store():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)

    async def fake_write_event(**kwargs):
        return None

    return ScheduleStore(factory, fake_write_event), engine


async def test_seeds_all_four_briefings():
    store, engine = await _store()
    try:
        agent_id = uuid.uuid4()
        created = await seed_claw_briefings(
            store, agent_id=agent_id, channel="sandbox", now=_now()
        )
        assert len(created) == len(BRIEFINGS) == 4
        for row in created:
            assert row.kind == "recurring"
            assert row.timezone == TIMEZONE
            assert row.agent_id == agent_id
            assert row.status == "active"
            assert row.cron_expression
            assert row.next_run_at is not None
        assert len({r.topic for r in created}) == 4
    finally:
        await engine.dispose()


async def test_seeding_is_idempotent():
    store, engine = await _store()
    try:
        agent_id = uuid.uuid4()
        first = await seed_claw_briefings(
            store, agent_id=agent_id, channel="sandbox", now=_now()
        )
        second = await seed_claw_briefings(
            store, agent_id=agent_id, channel="sandbox", now=_now()
        )
        assert len(first) == 4
        assert second == []
    finally:
        await engine.dispose()
