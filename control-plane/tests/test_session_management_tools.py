import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.archetype_catalog import ArchetypeCatalog
from control_plane.services.crypto import SecretBox
from control_plane.services.pool_store import PoolStore
from control_plane.services.session_store import SessionStore
from control_plane.tools.management import sessions as h
from control_plane.tools.management.context import ManagementToolContext


@pytest.fixture
async def ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield ManagementToolContext(
        registry=None, admin_client=None, payload_url="http://x", default_model="gpt-4o",
        invoking_message_text="", archetypes=ArchetypeCatalog(factory),
        pool=PoolStore(factory, SecretBox(Fernet.generate_key().decode())), sessions=SessionStore(factory),
    )
    await engine.dispose()


async def test_build_then_search_archetype(ctx):
    out = await h.build_archetype(
        {"name": "Researcher", "persona": "p", "allowed_tools": ["tavily_search"]}, ctx
    )
    assert "Researcher" in out
    assert "Researcher" in await h.search_archetypes({"query": "research"}, ctx)


async def test_spin_up_uses_saved_archetype_and_leases_a_bot(ctx):
    await h.build_archetype({"name": "Researcher", "persona": "p"}, ctx)
    await ctx.pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    out = await h.spin_up_session(
        {"archetype": "Researcher", "channel": "sandbox", "topic": "Bug 42", "display_name": "Scout"}, ctx
    )
    assert "Scout" in out and "sandbox" in out
    sess = await ctx.sessions.resolve_for_topic("sandbox", "Bug 42")
    assert sess is not None
    assert sess.archetype_snapshot["name"] == "Researcher"
    assert await ctx.pool.count_free() == 0


async def test_spin_up_one_off_without_saved_archetype(ctx):
    await ctx.pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    out = await h.spin_up_session(
        {"persona": "one-off helper", "channel": "c", "topic": "t", "display_name": "Tmp"}, ctx
    )
    assert "Tmp" in out
    sess = await ctx.sessions.resolve_for_topic("c", "t")
    assert sess.archetype_snapshot["persona"] == "one-off helper"


async def test_spin_up_reclaims_when_pool_empty(ctx):
    await h.build_archetype({"name": "R", "persona": "p"}, ctx)
    await ctx.pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    await h.spin_up_session({"archetype": "R", "channel": "c", "topic": "old", "display_name": "Old"}, ctx)
    old = await ctx.sessions.resolve_for_topic("c", "old")
    await ctx.sessions.mark_dormant(old.id)
    out = await h.spin_up_session({"archetype": "R", "channel": "c", "topic": "new", "display_name": "New"}, ctx)
    assert "New" in out
    assert (await ctx.sessions.resolve_for_topic("c", "old")) is None


async def test_spin_up_no_bot_and_nothing_reclaimable_rolls_back(ctx):
    # No pool bots seeded, no dormant sessions to reclaim.
    out = await h.spin_up_session(
        {"persona": "p", "channel": "c", "topic": "t", "display_name": "Z"}, ctx
    )
    assert "No pool bots are free" in out
    # The just-created session must have been closed (rolled back), not left live.
    assert (await ctx.sessions.resolve_for_topic("c", "t")) is None


async def test_close_session_frees_bot(ctx):
    await ctx.pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    await h.spin_up_session({"persona": "p", "channel": "c", "topic": "t", "display_name": "Z"}, ctx)
    out = await h.close_session({"channel": "c", "topic": "t"}, ctx)
    assert "closed" in out.lower()
    assert await ctx.pool.count_free() == 1
    assert (await ctx.sessions.resolve_for_topic("c", "t")) is None
