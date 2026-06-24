import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.crypto import SecretBox
from control_plane.services.pool_store import PoolStore
from control_plane.services.session_lifecycle import reclaim_for_capacity
from control_plane.services.session_store import SessionStore


@pytest.fixture
async def stores():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield PoolStore(factory, SecretBox(Fernet.generate_key().decode())), SessionStore(factory)
    await engine.dispose()


async def test_reclaims_oldest_dormant_and_frees_its_bot(stores):
    pool, sessions = stores
    bot = await pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    sess = await sessions.create(channel="c", topic="t", snapshot={}, pool_bot_id=bot.id)
    leased = await pool.lease(session_id=sess.id, display_name="Scout")
    assert leased is not None and await pool.count_free() == 0
    await sessions.mark_dormant(sess.id)

    freed = await reclaim_for_capacity(pool, sessions)
    assert freed is not None and freed.id == bot.id
    assert await pool.count_free() == 1
    assert (await sessions.resolve_for_topic("c", "t")) is None  # session closed


async def test_returns_none_when_no_dormant_sessions(stores):
    pool, sessions = stores
    assert await reclaim_for_capacity(pool, sessions) is None
