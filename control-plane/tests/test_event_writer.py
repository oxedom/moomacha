import uuid

import pytest
from sqlalchemy import select

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import EventRow
from control_plane.events.writer import write_event


@pytest.fixture
async def session_factory():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    yield factory
    await engine.dispose()


async def test_write_event_persists_row(session_factory):
    agent_id = uuid.uuid4()
    await write_event(
        session_factory,
        actor_type="agent",
        event_type="reply_posted",
        payload={"text": "hi"},
        related_agent_id=agent_id,
        related_channel="sandbox",
        source_message_id=112,
    )

    async with session_factory() as session:
        rows = (await session.execute(select(EventRow))).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "reply_posted"
        assert rows[0].related_agent_id == agent_id
        assert rows[0].payload == {"text": "hi"}
