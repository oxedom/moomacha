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

    def __init__(self):
        self.renamed: list[tuple] = []

    async def rename_bot(self, bot_id: int, full_name: str) -> None:
        self.renamed.append((bot_id, full_name))


class FakeBotClient:
    def __init__(self):
        self.subscriptions: list[str] = []
        self.messages: list[tuple] = []

    async def subscribe_to_channel(self, channel: str) -> None:
        self.subscriptions.append(channel)

    async def send_message(self, channel: str, topic: str, content: str) -> int:
        self.messages.append((channel, topic, content))
        return 999


def _make_ctx(factory, admin, fake_bot):
    return ManagementToolContext(
        registry=None,
        admin_client=admin,
        payload_url="http://x",
        default_model="gpt-4o",
        invoking_message_text="",
        archetypes=ArchetypeCatalog(factory),
        pool=PoolStore(factory, SecretBox(KEY)),
        sessions=SessionStore(factory),
        make_zulip_client=lambda s, e, k: fake_bot,
    )


@pytest.fixture
async def setup():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin = FakeAdmin()
    fake_bot = FakeBotClient()
    yield _make_ctx(factory, admin, fake_bot), admin, fake_bot
    await engine.dispose()


async def test_spin_up_renames_bot_subscribes_and_posts_kickoff(setup):
    ctx, admin, bot = setup
    await ctx.pool.seed(zulip_bot_id=77, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    out = await h.spin_up_session(
        {"persona": "p", "channel": "sandbox", "topic": "task-1", "display_name": "Scout"}, ctx
    )
    assert "Scout" in out
    assert admin.renamed == [(77, "Scout")]
    assert bot.subscriptions == ["sandbox"]
    assert any(
        m[0] == "sandbox" and m[1] == "task-1" and "Scout" in m[2] for m in bot.messages
    )


async def test_spin_up_without_admin_client_skips_zulip_actions(setup):
    ctx, admin, bot = setup
    ctx.admin_client = None
    await ctx.pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    out = await h.spin_up_session(
        {"persona": "p", "channel": "c", "topic": "t", "display_name": "X"}, ctx
    )
    assert "X" in out
    assert admin.renamed == []
    assert bot.subscriptions == []
    assert bot.messages == []
