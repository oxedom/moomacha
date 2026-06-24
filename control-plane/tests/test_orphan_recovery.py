import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.crypto import SecretBox
from control_plane.services.orphan_recovery import recover_pool_consistency
from control_plane.services.pool_store import PoolStore
from control_plane.services.session_store import SessionStore

KEY = Fernet.generate_key().decode()


@pytest.fixture
async def stores():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield PoolStore(factory, SecretBox(KEY)), SessionStore(factory)
    await engine.dispose()


async def test_recovers_abandoned_provisioning_and_is_idempotent(stores):
    pool, sessions = stores
    await pool.seed(zulip_bot_id=1, zulip_bot_email="w@x", api_key="k", outgoing_token="t")
    # Simulate a crash mid-spin_up: provisioning row + leased bot, never bound/marked live.
    s = await sessions.create(channel="c", topic="t", snapshot={}, pool_bot_id=None, state="provisioning")
    await pool.lease(session_id=s.id, display_name="X")

    await recover_pool_consistency(pool, sessions)

    assert await pool.count_free() == 1
    assert (await sessions.resolve_for_topic("c", "t")) is None  # provisioning row closed
    # Running again changes nothing.
    await recover_pool_consistency(pool, sessions)
    assert await pool.count_free() == 1


async def test_releases_orphan_lease_when_session_closed(stores):
    pool, sessions = stores
    await pool.seed(zulip_bot_id=1, zulip_bot_email="w@x", api_key="k", outgoing_token="t")
    s = await sessions.create(channel="c", topic="t", snapshot={}, pool_bot_id=None, state="live")
    leased = await pool.lease(session_id=s.id, display_name="X")
    await sessions.bind_pool_bot(s.id, leased.id)
    await sessions.close(s.id)  # session closed but bot still leased (close-race orphan)

    await recover_pool_consistency(pool, sessions)

    assert await pool.count_free() == 1


async def test_leaves_a_healthy_live_session_untouched(stores):
    pool, sessions = stores
    await pool.seed(zulip_bot_id=1, zulip_bot_email="w@x", api_key="k", outgoing_token="t")
    s = await sessions.create(channel="c", topic="t", snapshot={}, pool_bot_id=None, state="live")
    leased = await pool.lease(session_id=s.id, display_name="X")
    await sessions.bind_pool_bot(s.id, leased.id)

    await recover_pool_consistency(pool, sessions)

    assert await pool.count_free() == 0  # still leased
    assert (await sessions.resolve_for_topic("c", "t")) is not None
