import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.crypto import SecretBox
from control_plane.services.pool_store import PoolStore

KEY = Fernet.generate_key().decode()


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield PoolStore(factory, SecretBox(KEY))
    await engine.dispose()


async def test_find_leased_lists_only_leased(store):
    await store.seed(zulip_bot_id=1, zulip_bot_email="a@x", api_key="k", outgoing_token="t")
    await store.seed(zulip_bot_id=2, zulip_bot_email="b@x", api_key="k", outgoing_token="t")
    sid = uuid.uuid4()
    leased = await store.lease(session_id=sid, display_name="X")
    found = await store.find_leased()
    assert [b.id for b in found] == [leased.id]


async def test_release_for_session_frees_the_bot(store):
    await store.seed(zulip_bot_id=1, zulip_bot_email="a@x", api_key="k", outgoing_token="t")
    sid = uuid.uuid4()
    await store.lease(session_id=sid, display_name="X")
    assert await store.count_free() == 0
    await store.release_for_session(sid)
    assert await store.count_free() == 1
    assert await store.find_leased() == []


async def test_release_for_session_noop_when_no_match(store):
    await store.seed(zulip_bot_id=1, zulip_bot_email="a@x", api_key="k", outgoing_token="t")
    await store.release_for_session(uuid.uuid4())  # must not raise
    assert await store.count_free() == 1
