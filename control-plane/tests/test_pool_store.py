import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.crypto import SecretBox
from control_plane.services.pool_store import PoolStore


@pytest.fixture
async def pool():
    key = Fernet.generate_key().decode()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield PoolStore(async_sessionmaker(engine, expire_on_commit=False), SecretBox(key))
    await engine.dispose()


async def test_seed_then_lease_then_release(pool):
    await pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    assert await pool.count_free() == 1
    sid = uuid.uuid4()
    leased = await pool.lease(session_id=sid, display_name="Scout")
    assert leased is not None and leased.status == "leased"
    assert leased.current_name == "Scout" and leased.current_session_id == sid
    assert await pool.count_free() == 0
    await pool.release(leased.id)
    assert await pool.count_free() == 1
    freed = await pool.get(leased.id)
    assert freed.current_name is None and freed.current_session_id is None


async def test_lease_returns_none_when_pool_empty(pool):
    assert await pool.lease(session_id=uuid.uuid4(), display_name="X") is None


async def test_creds_are_encrypted_at_rest(pool):
    await pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="secret-key", outgoing_token="t")
    row = (await pool.list_all())[0]
    assert "secret-key" not in row.zulip_api_key_encrypted
    creds = await pool.resolve_creds(row.id)
    assert creds.api_key == "secret-key"
