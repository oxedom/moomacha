import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.crypto import SecretBox
from control_plane.services.pool_store import PoolStore
from control_plane.services.session_store import SessionStore
from control_plane.services.pool_resolver import (
    PoolBotNoSession,
    PoolBotTurnResult,
    resolve_pool_bot_for_webhook,
)

KEY = Fernet.generate_key().decode()


@pytest.fixture
async def stores():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield PoolStore(factory, SecretBox(KEY)), SessionStore(factory)
    await engine.dispose()


async def test_unknown_email_returns_none(stores):
    pool, sessions = stores
    result = await resolve_pool_bot_for_webhook(pool, sessions, "not-a-pool-bot@x", "c", "t")
    assert result is None


async def test_pool_bot_with_no_session_returns_no_session(stores):
    pool, sessions = stores
    await pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="tok")
    result = await resolve_pool_bot_for_webhook(pool, sessions, "w1@x", "sandbox", "Bug 42")
    assert isinstance(result, PoolBotNoSession)
    assert result.outgoing_token == "tok"


async def test_pool_bot_with_live_session_returns_turn_result(stores):
    pool, sessions = stores
    bot = await pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="tok")
    sess = await sessions.create(
        channel="sandbox", topic="Bug 42",
        snapshot={
            "name": "Researcher",
            "persona": "You research things.",
            "model_id": "gpt-4o",
            "context_message_count": 20,
            "allowed_tools": ["tavily_search"],
            "runtime_kind": "deepagents",
            "runtime_config": {},
        },
        pool_bot_id=bot.id,
    )
    result = await resolve_pool_bot_for_webhook(pool, sessions, "w1@x", "sandbox", "Bug 42")

    assert isinstance(result, PoolBotTurnResult)
    assert result.session_id == sess.id
    assert result.outgoing_token == "tok"
    assert result.agent.persona == "You research things."
    assert result.agent.allowed_tools == ["tavily_search"]
    assert result.agent.runtime_kind == "deepagents"
    assert result.agent.is_bastion is False
    assert result.agent.zulip_bot_email == "w1@x"


async def test_dormant_session_is_reopened(stores):
    pool, sessions = stores
    bot = await pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="tok")
    sess = await sessions.create(
        channel="c", topic="t",
        snapshot={"name": "R", "persona": "p"},
        pool_bot_id=bot.id,
    )
    await sessions.mark_dormant(sess.id)

    result = await resolve_pool_bot_for_webhook(pool, sessions, "w1@x", "c", "t")

    assert isinstance(result, PoolBotTurnResult)
    live = await sessions.resolve_for_topic("c", "t")
    assert live.state == "live"


async def test_dormant_reopen_returning_none_yields_no_session(stores):
    # If the session row vanishes between resolve and reopen, reopen returns None;
    # the resolver must treat this as no active session, not raise AttributeError.
    pool, sessions = stores
    bot = await pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="tok")
    sess = await sessions.create(
        channel="c", topic="t",
        snapshot={"name": "R", "persona": "p"},
        pool_bot_id=bot.id,
    )
    await sessions.mark_dormant(sess.id)

    async def _reopen_none(_session_id):
        return None

    sessions.reopen = _reopen_none

    result = await resolve_pool_bot_for_webhook(pool, sessions, "w1@x", "c", "t")

    assert isinstance(result, PoolBotNoSession)
    assert result.outgoing_token == "tok"
