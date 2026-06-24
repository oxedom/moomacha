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

KEY = Fernet.generate_key().decode()


class FakeAdmin:
    site = "https://example.zulipchat.com"

    async def rename_bot(self, bot_id, full_name):
        return None


class ExplodingBotClient:
    async def subscribe_to_channel(self, channel):
        raise RuntimeError("zulip is down")

    async def send_message(self, channel, topic, content):
        return 1


class GoodBotClient:
    async def subscribe_to_channel(self, channel):
        return None

    async def send_message(self, channel, topic, content):
        return 1


def _ctx(factory, bot_client):
    return ManagementToolContext(
        registry=None, admin_client=FakeAdmin(), payload_url="http://x",
        default_model="gpt-4o", invoking_message_text="",
        archetypes=ArchetypeCatalog(factory),
        pool=PoolStore(factory, SecretBox(KEY)), sessions=SessionStore(factory),
        make_zulip_client=lambda s, e, k: bot_client,
    )


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_birth_failure_rolls_back_bot_and_session(factory):
    ctx = _ctx(factory, ExplodingBotClient())
    await ctx.pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    out = await h.spin_up_session(
        {"persona": "p", "channel": "c", "topic": "t", "display_name": "Z"}, ctx
    )
    assert "rolled back" in out
    assert await ctx.pool.count_free() == 1                         # bot released
    assert (await ctx.sessions.resolve_for_topic("c", "t")) is None  # session closed


async def test_successful_spin_up_marks_session_live(factory):
    ctx = _ctx(factory, GoodBotClient())
    await ctx.pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    out = await h.spin_up_session(
        {"persona": "p", "channel": "c", "topic": "t", "display_name": "Z"}, ctx
    )
    assert "Z" in out
    sess = await ctx.sessions.resolve_for_topic("c", "t")
    assert sess is not None and sess.state == "live"
    assert await ctx.pool.count_free() == 0
