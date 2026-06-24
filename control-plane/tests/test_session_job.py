import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.crypto import SecretBox
from control_plane.services.job_queue import Job, JobDeps, process_job
from control_plane.services.pool_store import PoolStore
from control_plane.services.session_store import SessionStore

FERNET_KEY = Fernet.generate_key().decode()


class FakeAgentClient:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.next_id = 100

    async def send_message(self, channel, topic, content):
        self.sent.append((channel, topic, content))
        return self.next_id

    async def send_direct_message(self, recipient_ids, content):
        return self.next_id

    async def get_messages(self, channel, topic, num_before):
        return [{"sender_full_name": "Alice", "content": "hello"}]

    async def get_direct_messages(self, recipient_ids, num_before):
        return [{"sender_full_name": "Alice", "content": "hello"}]

    async def get_channel_messages(self, channel, num_before):
        return []

    async def update_message(self, message_id, content):
        self.updated.append((message_id, content))


class FakeLLM:
    def __init__(self, reply):
        self._reply = reply
        self.closed = False

    @property
    def chat(self):
        from types import SimpleNamespace
        return SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        from types import SimpleNamespace
        msg = SimpleNamespace(content=self._reply, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    async def close(self):
        self.closed = True


@pytest.fixture
async def db_and_stores():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pool = PoolStore(factory, SecretBox(FERNET_KEY))
    sessions = SessionStore(factory)
    yield factory, pool, sessions
    await engine.dispose()


async def test_session_job_resolves_agent_from_snapshot(db_and_stores):
    factory, pool, sessions = db_and_stores
    bot = await pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    sess = await sessions.create(
        channel="sandbox", topic="task-1",
        snapshot={
            "name": "Researcher",
            "persona": "You are a researcher.",
            "model_id": "gpt-4o",
            "context_message_count": 10,
            "allowed_tools": [],
            "runtime_kind": "openai_tool_loop",
            "runtime_config": {},
        },
        pool_bot_id=bot.id,
    )

    fake_client = FakeAgentClient()
    registry = ToolRegistry()
    runtime = ToolRuntime(registry)

    deps = JobDeps(
        session_factory=factory,
        resolve_agent=lambda aid: None,  # must NOT be called for session turns
        make_agent_client=lambda e, k: fake_client,
        tool_registry=registry,
        tool_runtime=runtime,
        client_factory=lambda key, url: FakeLLM("session reply"),
        llm_api_key="sk-x",
        llm_base_url=None,
        max_tool_calls=5,
        context_default_n=10,
        pool_store=pool,
        session_store=sessions,
    )
    job = Job(
        agent_id=bot.id,
        channel="sandbox",
        topic="task-1",
        content="help me",
        session_id=sess.id,
    )
    await process_job(job, deps)

    assert fake_client.sent
    assert any("session reply" in u[1] for u in fake_client.updated)


async def test_session_job_touches_session_after_turn(db_and_stores):
    factory, pool, sessions = db_and_stores
    bot = await pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    old = datetime.now(UTC) - timedelta(hours=5)
    sess = await sessions.create(
        channel="c", topic="t",
        snapshot={"name": "R", "persona": "p", "runtime_kind": "openai_tool_loop", "runtime_config": {}},
        pool_bot_id=bot.id,
    )
    await sessions.touch(sess.id, when=old)

    fake_client = FakeAgentClient()
    registry = ToolRegistry()
    runtime = ToolRuntime(registry)
    deps = JobDeps(
        session_factory=factory,
        resolve_agent=lambda aid: None,
        make_agent_client=lambda e, k: fake_client,
        tool_registry=registry,
        tool_runtime=runtime,
        client_factory=lambda key, url: FakeLLM("ok"),
        llm_api_key="sk-x",
        llm_base_url=None,
        max_tool_calls=5,
        context_default_n=10,
        pool_store=pool,
        session_store=sessions,
    )
    job = Job(agent_id=bot.id, channel="c", topic="t", content="hi", session_id=sess.id)
    await process_job(job, deps)

    updated_sess = await sessions.resolve_for_topic("c", "t")
    assert updated_sess.last_active_at > old


async def test_non_session_job_uses_resolve_agent_as_before(db_and_stores):
    factory, pool, sessions = db_and_stores
    resolved_calls = []
    agent_id = uuid.uuid4()

    async def fake_resolve(aid):
        resolved_calls.append(aid)
        return ResolvedAgent(
            id=aid, name="echo", persona="p", model_id="gpt-4o",
            zulip_bot_id=1, zulip_bot_email="e@x", zulip_api_key="k",
            zulip_outgoing_token="t", context_message_count=10, readable_channels=[],
        )

    fake_client = FakeAgentClient()
    registry = ToolRegistry()
    runtime = ToolRuntime(registry)
    deps = JobDeps(
        session_factory=factory,
        resolve_agent=fake_resolve,
        make_agent_client=lambda e, k: fake_client,
        tool_registry=registry,
        tool_runtime=runtime,
        client_factory=lambda key, url: FakeLLM("hi"),
        llm_api_key="sk-x",
        llm_base_url=None,
        max_tool_calls=5,
        context_default_n=10,
        pool_store=pool,
        session_store=sessions,
    )
    job = Job(agent_id=agent_id, channel="c", topic="t", content="hi")  # no session_id
    await process_job(job, deps)

    assert resolved_calls == [agent_id]
