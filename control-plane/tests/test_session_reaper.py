import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.session_reaper import SessionReaperDeps, SessionReaperLoop
from control_plane.services.session_store import SessionStore


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield SessionStore(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def test_tick_marks_idle_session_dormant(store):
    sess = await store.create(channel="c", topic="t", snapshot={}, pool_bot_id=None)
    await store.touch(sess.id, when=datetime.now(UTC) - timedelta(hours=2))

    deps = SessionReaperDeps(store=store, clock=lambda: datetime.now(UTC), idle_seconds=3600)
    await SessionReaperLoop(deps).tick()

    updated = await store.resolve_for_topic("c", "t")
    assert updated.state == "dormant"


async def test_tick_leaves_recently_active_session_live(store):
    await store.create(channel="c", topic="t", snapshot={}, pool_bot_id=None)

    deps = SessionReaperDeps(store=store, clock=lambda: datetime.now(UTC), idle_seconds=3600)
    await SessionReaperLoop(deps).tick()

    sess = await store.resolve_for_topic("c", "t")
    assert sess.state == "live"


async def test_tick_skips_already_dormant_sessions(store):
    sess = await store.create(channel="c", topic="t", snapshot={}, pool_bot_id=None)
    await store.mark_dormant(sess.id)

    deps = SessionReaperDeps(store=store, clock=lambda: datetime.now(UTC), idle_seconds=0)
    await SessionReaperLoop(deps).tick()
    updated = await store.resolve_for_topic("c", "t")
    assert updated.state == "dormant"  # unchanged


async def test_run_forever_ticks_repeatedly_until_cancelled(store):
    deps = SessionReaperDeps(store=store, clock=lambda: datetime.now(UTC), idle_seconds=3600)
    tick_count = []

    original_tick = SessionReaperLoop.tick

    async def counting_tick(self):
        tick_count.append(1)
        await original_tick(self)

    SessionReaperLoop.tick = counting_tick
    task = asyncio.create_task(SessionReaperLoop(deps).run_forever(0))
    while len(tick_count) < 2:
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(tick_count) >= 2
    SessionReaperLoop.tick = original_tick
