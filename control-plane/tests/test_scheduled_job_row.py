import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import ScheduledJobRow


async def test_scheduled_job_row_roundtrips():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    try:
        async with factory() as session:
            row = ScheduledJobRow(
                agent_id=uuid.uuid4(),
                channel="sandbox",
                topic="standup",
                kind="recurring",
                cron_expression="0 9 * * 1-5",
                timezone="UTC",
                instruction="post the standup nudge",
                status="active",
                next_run_at=datetime(2026, 5, 25, 9, 0, tzinfo=UTC),
            )
            session.add(row)
            await session.commit()
            rid = row.id

        async with factory() as session:
            got = (await session.execute(select(ScheduledJobRow))).scalar_one()
        assert got.id == rid
        assert got.kind == "recurring"
        assert got.status == "active"
        assert got.run_at is None
        assert got.last_run_at is None
    finally:
        await engine.dispose()
