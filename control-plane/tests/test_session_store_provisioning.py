import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.session_store import SessionStore


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield SessionStore(factory)
    await engine.dispose()


async def test_create_defaults_to_live(store):
    s = await store.create(channel="c", topic="t", snapshot={}, pool_bot_id=None)
    assert s.state == "live"


async def test_create_with_provisioning_then_mark_live(store):
    s = await store.create(
        channel="c", topic="t", snapshot={}, pool_bot_id=None, state="provisioning"
    )
    assert s.state == "provisioning"
    live = await store.mark_live(s.id)
    assert live is not None and live.state == "live"


async def test_list_by_state(store):
    a = await store.create(channel="c", topic="a", snapshot={}, pool_bot_id=None, state="provisioning")
    await store.create(channel="c", topic="b", snapshot={}, pool_bot_id=None, state="live")
    prov = await store.list_by_state("provisioning")
    assert [r.id for r in prov] == [a.id]


async def test_get_returns_row_or_none(store):
    s = await store.create(channel="c", topic="t", snapshot={}, pool_bot_id=None)
    got = await store.get(s.id)
    assert got is not None and got.id == s.id
    assert await store.get(uuid.uuid4()) is None
