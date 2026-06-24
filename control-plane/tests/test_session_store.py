from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.session_store import SessionStore


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield SessionStore(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def test_create_sets_memory_ns_and_resolves(store):
    sess = await store.create(channel="sandbox", topic="Bug 42", snapshot={"name": "R"}, pool_bot_id=None)
    assert sess.memory_ns == f"agent:session:{sess.id}"
    found = await store.resolve_for_topic("sandbox", "Bug 42")
    assert found is not None and found.id == sess.id


async def test_resolve_ignores_closed(store):
    sess = await store.create(channel="c", topic="t", snapshot={}, pool_bot_id=None)
    await store.close(sess.id)
    assert await store.resolve_for_topic("c", "t") is None


async def test_dormant_then_reopen(store):
    sess = await store.create(channel="c", topic="t", snapshot={}, pool_bot_id=None)
    await store.mark_dormant(sess.id)
    assert (await store.resolve_for_topic("c", "t")).state == "dormant"
    reopened = await store.reopen(sess.id)
    assert reopened.state == "live"


async def test_find_idle_and_oldest_dormant(store):
    old = datetime.now(UTC) - timedelta(hours=5)
    a = await store.create(channel="c", topic="a", snapshot={}, pool_bot_id=None)
    await store.touch(a.id, when=old)
    idle = await store.find_idle(now=datetime.now(UTC), idle_seconds=3600)
    assert [s.id for s in idle] == [a.id]
    await store.mark_dormant(a.id)
    assert (await store.oldest_dormant()).id == a.id


async def test_create_sets_archetype_memory_ns(store):
    row = await store.create(
        channel="sandbox", topic="T", snapshot={"name": "researcher"},
        pool_bot_id=None, archetype_name="researcher",
    )
    assert row.memory_ns == "agent:archetype:researcher"


async def test_create_one_off_uses_session_memory_ns(store):
    row = await store.create(
        channel="sandbox", topic="T2", snapshot={"name": "one-off"}, pool_bot_id=None,
    )
    assert row.memory_ns == f"agent:session:{row.id}"
