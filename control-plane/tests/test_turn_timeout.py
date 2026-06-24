import asyncio
import uuid

import pytest
from sqlalchemy import select

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import EventRow
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.job_queue import Job, JobDeps, process_job


class FakeAgentClient:
    def __init__(self):
        self.updated = []

    async def send_message(self, channel, topic, content):
        return 555

    async def get_messages(self, channel, topic, num_before):
        return []

    async def update_message(self, message_id, content):
        self.updated.append((message_id, content))


class HangingRunner:
    async def run(self, inp):
        await asyncio.sleep(5)  # far longer than the timeout under test
        return "never"


class HangingRouter:
    def select(self, agent):
        return HangingRunner()


class FakeLLM:
    async def close(self):
        pass


def _agent():
    return ResolvedAgent(
        id=uuid.uuid4(), name="researcher", persona="be helpful", model_id="gpt-4o",
        zulip_bot_email="r-bot@x", zulip_api_key="botkey", zulip_outgoing_token="tok",
        context_message_count=20, readable_channels=["sandbox"], allowed_tools=[],
    )


@pytest.fixture
async def session_factory():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    yield factory
    await engine.dispose()


async def test_turn_timeout_fails_the_job_and_worker_survives(session_factory):
    agent = _agent()
    client = FakeAgentClient()
    registry = ToolRegistry()

    async def fake_resolve(agent_id):
        return agent

    deps = JobDeps(
        session_factory=session_factory,
        resolve_agent=fake_resolve,
        make_agent_client=lambda email, key: client,
        tool_registry=registry,
        tool_runtime=ToolRuntime(registry),
        client_factory=lambda key, url: FakeLLM(),
        llm_api_key="x", llm_base_url=None, max_tool_calls=10, context_default_n=20,
        runner_router=HangingRouter(),
        turn_timeout_seconds=0.05,
    )
    job = Job(agent_id=agent.id, channel="sandbox", topic="t", source_message_id=1, content="hi")

    await process_job(job, deps)

    assert any("timed out" in content for _, content in client.updated)
    async with session_factory() as s:
        types = {e.event_type for e in (await s.execute(select(EventRow))).scalars()}
    assert "error" in types
    assert "turn.end" in types
