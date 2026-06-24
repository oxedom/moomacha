import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import ArchetypeRow, Base, PoolBotRow, SessionRow


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_round_trip_all_three(factory):
    async with factory() as s:
        arch = ArchetypeRow(name="Researcher", persona="p")
        bot = PoolBotRow(
            zulip_bot_id=1, zulip_bot_email="w1@x", zulip_api_key_encrypted="e",
            zulip_outgoing_token_encrypted="t",
        )
        s.add_all([arch, bot])
        await s.commit()
        sess = SessionRow(
            channel="sandbox", topic="bug-1", archetype_snapshot={"name": "Researcher"},
            pool_bot_id=bot.id, memory_ns="sandbox-bug-1",
        )
        s.add(sess)
        await s.commit()

    async with factory() as s:
        got = await s.get(SessionRow, sess.id)
        assert got.state == "live"
        assert got.granted_caps == []
        assert got.archetype_snapshot == {"name": "Researcher"}
        gotbot = await s.get(PoolBotRow, bot.id)
        assert gotbot.status == "free"
        assert isinstance(gotbot.id, uuid.UUID)
        gotarch = await s.get(ArchetypeRow, arch.id)
        assert gotarch.runtime_kind == "deepagents"
