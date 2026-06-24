"""Test that process_job routes through an injected AgentRunnerRouter."""
import uuid
from typing import Any

import pytest

from control_plane.db.engine import build_session_factory, create_all
from control_plane.runtime.runners.base import RunnerInput
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime
from control_plane.runtime.tools.messages import register_message_tools
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.job_queue import Job, JobDeps, process_job


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeAgentClient:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.next_id = 999

    async def send_message(self, channel, topic, content):
        self.sent.append((channel, topic, content))
        return self.next_id

    async def send_direct_message(self, recipient_ids, content):
        return self.next_id

    async def get_messages(self, channel, topic, num_before):
        return [{"sender_full_name": "Alice", "content": "hello agent"}]

    async def get_direct_messages(self, recipient_ids, num_before):
        return [{"sender_full_name": "Alice", "content": "hello bot"}]

    async def get_channel_messages(self, channel, num_before):
        return []

    async def update_message(self, message_id, content):
        self.updated.append((message_id, content))


class FakeLLM:
    """Minimal stub — should NOT be called when the fake runner is used."""
    async def close(self):
        pass


class RecordingRunner:
    """Captures the RunnerInput it receives and returns a fixed answer."""

    def __init__(self, answer: str):
        self.answer = answer
        self.captured: RunnerInput | None = None

    async def run(self, inp: RunnerInput) -> str:
        self.captured = inp
        return self.answer


class FakeRouter:
    """Returns the RecordingRunner regardless of the agent passed to select()."""

    def __init__(self, runner: RecordingRunner):
        self._runner = runner

    def select(self, agent: Any) -> RecordingRunner:
        return self._runner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def session_factory():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    yield factory
    await engine.dispose()


def _deepagent_agent():
    return ResolvedAgent(
        id=uuid.uuid4(),
        name="deep",
        persona="I am a deep agent",
        model_id="gpt-4o",
        zulip_bot_email="deep@x",
        zulip_api_key="deepkey",
        zulip_outgoing_token="tok",
        context_message_count=20,
        readable_channels=["sandbox"],
        allowed_tools=[],
        runtime_kind="deepagents",
    )


def _deps(session_factory, agent, fake_client, llm, *, runner_router=None):
    registry = ToolRegistry()
    register_message_tools(registry)

    async def fake_resolve(agent_id):
        return agent

    return JobDeps(
        session_factory=session_factory,
        resolve_agent=fake_resolve,
        make_agent_client=lambda email, key: fake_client,
        tool_registry=registry,
        tool_runtime=ToolRuntime(registry),
        client_factory=lambda key, url: llm,
        llm_api_key="sk-x",
        llm_base_url=None,
        max_tool_calls=10,
        context_default_n=20,
        runner_router=runner_router,
    )


def _job(agent_id):
    return Job(
        agent_id=agent_id,
        channel="sandbox",
        topic="deep-topic",
        source_message_id=200,
        content="@**deep** do something",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_process_job_uses_injected_router_runner(session_factory):
    """process_job should call the runner selected by the injected router."""
    agent = _deepagent_agent()
    fake_client = FakeAgentClient()
    llm = FakeLLM()
    recording_runner = RecordingRunner("DEEP-ANSWER")
    fake_router = FakeRouter(recording_runner)

    deps = _deps(session_factory, agent, fake_client, llm, runner_router=fake_router)
    job = _job(agent.id)

    await process_job(job, deps)

    # The placeholder was edited with the runner's returned text
    assert fake_client.updated == [(999, "DEEP-ANSWER")]

    # The runner captured a well-formed RunnerInput
    assert recording_runner.captured is not None
    assert recording_runner.captured.user_message == job.content
    assert recording_runner.captured.system_prompt  # non-empty


async def test_process_job_injects_tool_list_into_system_prompt(session_factory):
    """The agent's allowed tools are listed in the system prompt it receives."""
    agent = _deepagent_agent()
    agent.allowed_tools = ["read_topic"]
    fake_client = FakeAgentClient()
    recording_runner = RecordingRunner("OK")
    fake_router = FakeRouter(recording_runner)

    deps = _deps(session_factory, agent, fake_client, FakeLLM(), runner_router=fake_router)
    await process_job(_job(agent.id), deps)

    prompt = recording_runner.captured.system_prompt
    assert "## Your tools" in prompt
    assert "- read_topic —" in prompt


async def test_process_job_no_router_falls_back_to_openai_loop(session_factory):
    """When runner_router=None, process_job builds the default openai router.

    We use a FakeLLM that returns a scripted answer to prove the openai loop
    still executes (no-regression proof for existing callers).
    """
    from types import SimpleNamespace

    class ScriptedLLM:
        def __init__(self):
            self.closed = False

        @property
        def chat(self):
            return SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        async def _create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="openai-answer", tool_calls=None)
                )]
            )

        async def close(self):
            self.closed = True

    agent = ResolvedAgent(
        id=uuid.uuid4(),
        name="openai-agent",
        persona="I use openai",
        model_id="gpt-4o",
        zulip_bot_email="oa@x",
        zulip_api_key="k",
        zulip_outgoing_token="t",
        context_message_count=20,
        readable_channels=["sandbox"],
        allowed_tools=[],
        runtime_kind="openai_tool_loop",
    )
    fake_client = FakeAgentClient()
    llm = ScriptedLLM()

    deps = _deps(session_factory, agent, fake_client, llm, runner_router=None)
    job = Job(
        agent_id=agent.id,
        channel="sandbox",
        topic="t",
        source_message_id=201,
        content="hello",
    )

    await process_job(job, deps)

    assert fake_client.updated == [(999, "openai-answer")]


async def test_process_job_unknown_runtime_kind_fails_closed(session_factory):
    """An agent with an unregistered runtime_kind must fail closed: a user-facing
    error edit + an error event + turn.end, not a raw traceback."""
    from sqlalchemy import select as _select
    from control_plane.db.tables import EventRow

    agent = _deepagent_agent()
    agent.runtime_kind = "not_a_real_runtime"
    fake_client = FakeAgentClient()
    # runner_router=None -> default openai-only router; "not_a_real_runtime" unknown.
    deps = _deps(session_factory, agent, fake_client, FakeLLM(), runner_router=None)

    await process_job(_job(agent.id), deps)

    assert len(fake_client.updated) == 1
    assert "runtime" in fake_client.updated[0][1].lower()

    async with session_factory() as session:
        rows = (await session.execute(_select(EventRow))).scalars().all()
    error_rows = [r for r in rows if r.event_type == "error"]
    assert len(error_rows) == 1
    assert error_rows[0].payload["error_type"] == "unknown_runtime_kind"
    assert "not_a_real_runtime" in error_rows[0].payload["message"]
    turn_end_rows = [r for r in rows if r.event_type == "turn.end"]
    assert len(turn_end_rows) == 1
    assert turn_end_rows[0].status == "failed"
