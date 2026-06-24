import uuid

import pytest
from sqlalchemy import select

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import AgentRow, EventRow


@pytest.fixture
async def session_factory():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    yield factory
    await engine.dispose()


async def test_agent_round_trip(session_factory):
    agent_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            AgentRow(
                id=agent_id,
                name="researcher",
                persona="You research things.",
                model_id="gpt-4o",
                zulip_bot_id=42,
                zulip_bot_email="researcher-bot@example.zulipchat.com",
                zulip_api_key_encrypted="enc",
                zulip_outgoing_token_encrypted="tok",
                context_message_count=20,
                readable_channels=["sandbox", "engineering"],
                provisioning_status="active",
            )
        )
        await session.commit()

    async with session_factory() as session:
        row = await session.get(AgentRow, agent_id)
        assert row.readable_channels == ["sandbox", "engineering"]
        assert row.model_id == "gpt-4o"


async def test_event_round_trip(session_factory):
    async with session_factory() as session:
        session.add(
            EventRow(
                actor_type="system",
                event_type="webhook_received",
                payload={"message_id": 112},
                source_message_id=112,
            )
        )
        await session.commit()

    async with session_factory() as session:
        rows = (await session.execute(select(EventRow))).scalars().all()
        assert len(rows) == 1
        assert rows[0].payload == {"message_id": 112}


async def test_event_row_has_observability_columns():
    from control_plane.db.engine import build_session_factory, create_all
    from control_plane.db.tables import EventRow

    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    async with factory() as s:
        s.add(EventRow(
            actor_type="agent", event_type="turn.start", payload={},
            trace_id="tr-1", turn_id="tn-1", seq=0, duration_ms=None, status=None,
        ))
        await s.commit()
    cols = {c.name for c in EventRow.__table__.columns}
    assert {"trace_id", "turn_id", "seq", "duration_ms", "status"} <= cols
