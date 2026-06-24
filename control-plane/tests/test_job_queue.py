import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import EventRow
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime
from control_plane.runtime.tools.messages import register_message_tools
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.job_queue import Job, JobDeps, process_job


class FakeAgentClient:
    def __init__(self):
        self.sent = []
        self.direct_sent = []
        self.updated = []
        self.direct_history_requests = []
        self.next_id = 555

    async def send_message(self, channel, topic, content):
        self.sent.append((channel, topic, content))
        return self.next_id

    async def send_direct_message(self, recipient_ids, content):
        self.direct_sent.append((recipient_ids, content))
        return self.next_id

    async def get_messages(self, channel, topic, num_before):
        return [{"sender_full_name": "Alice", "content": "hello agent"}]

    async def get_direct_messages(self, recipient_ids, num_before):
        self.direct_history_requests.append((recipient_ids, num_before))
        return [{"sender_full_name": "Alice", "content": "hello bot"}]

    async def get_channel_messages(self, channel, num_before):
        return [{"sender_full_name": "Alice", "content": "channel msg"}]

    async def update_message(self, message_id, content):
        self.updated.append((message_id, content))


def _tool_call(id_, name, args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=args))


class FakeLLM:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.closed = False

    @property
    def chat(self):
        return SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(choices=[SimpleNamespace(message=item)])

    async def close(self):
        self.closed = True


def _final(text):
    return SimpleNamespace(content=text, tool_calls=None)


@pytest.fixture
async def session_factory():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    yield factory
    await engine.dispose()


def _agent(allowed_tools=None):
    return ResolvedAgent(
        id=uuid.uuid4(),
        name="researcher",
        persona="be helpful",
        model_id="gpt-4o",
        zulip_bot_email="r-bot@x",
        zulip_api_key="botkey",
        zulip_outgoing_token="tok",
        context_message_count=20,
        readable_channels=["sandbox"],
        allowed_tools=allowed_tools or [],
    )


def _deps(session_factory, agent, fake_client, llm, *, max_tool_calls=10):
    registry = ToolRegistry()
    register_message_tools(registry)

    async def fake_resolve(agent_id):
        return agent if agent is not None else None

    return JobDeps(
        session_factory=session_factory,
        resolve_agent=fake_resolve,
        make_agent_client=lambda email, key: fake_client,
        tool_registry=registry,
        tool_runtime=ToolRuntime(registry),
        client_factory=lambda key, url: llm,
        llm_api_key="sk-openai-x",
        llm_base_url=None,
        max_tool_calls=max_tool_calls,
        context_default_n=20,
    )


def _job(agent_id):
    return Job(
        agent_id=agent_id,
        channel="sandbox",
        topic="greetings",
        source_message_id=112,
        content="@**researcher** hi",
    )


async def _event_types(session_factory):
    async with session_factory() as session:
        return {e.event_type for e in (await session.execute(select(EventRow))).scalars()}


async def test_process_job_no_tools_posts_progress_then_final(session_factory):
    agent = _agent()
    fake_client = FakeAgentClient()
    llm = FakeLLM([_final("here is your answer")])
    deps = _deps(session_factory, agent, fake_client, llm)

    await process_job(_job(agent.id), deps)

    assert fake_client.sent[0][2].startswith("🤔")
    assert fake_client.updated == [(555, "here is your answer")]
    assert {"turn.start", "turn.end"} <= await _event_types(session_factory)
    assert llm.closed is True


async def test_process_job_direct_message_posts_and_fetches_dm_context(session_factory):
    agent = _agent()
    fake_client = FakeAgentClient()
    llm = FakeLLM([_final("direct answer")])
    deps = _deps(session_factory, agent, fake_client, llm)

    await process_job(
        Job(
            agent_id=agent.id,
            channel="direct",
            topic="",
            content="hi bot",
            conversation_type="direct",
            direct_recipient_ids=[7],
            source_message_id=113,
        ),
        deps,
    )

    assert fake_client.sent == []
    assert fake_client.direct_sent == [([7], "🤔 Working on it…")]
    assert fake_client.direct_history_requests == [([7], 20)]
    assert fake_client.updated == [(555, "direct answer")]


async def test_process_job_runs_tool_then_answers_and_logs_tool_called(session_factory):
    agent = _agent(allowed_tools=["read_topic"])
    fake_client = FakeAgentClient()
    llm = FakeLLM([
        SimpleNamespace(
            content=None,
            tool_calls=[_tool_call("c1", "read_topic",
                                    '{"channel": "sandbox", "topic": "greetings", "limit": 5}')],
        ),
        _final("answer that used the tool"),
    ])
    deps = _deps(session_factory, agent, fake_client, llm)

    await process_job(_job(agent.id), deps)

    assert fake_client.updated == [(555, "answer that used the tool")]
    assert {"turn.start", "tool.call", "turn.end"} <= await _event_types(session_factory)


async def test_process_job_budget_exceeded_posts_limit_message(session_factory):
    agent = _agent(allowed_tools=["read_topic"])
    fake_client = FakeAgentClient()
    asking = [
        SimpleNamespace(content=None,
                        tool_calls=[_tool_call("c", "read_topic",
                                               '{"channel": "sandbox", "topic": "t", "limit": 1}')])
        for _ in range(5)
    ]
    llm = FakeLLM(asking)
    deps = _deps(session_factory, agent, fake_client, llm, max_tool_calls=2)

    await process_job(_job(agent.id), deps)  # must not raise

    assert "tool-call limit" in fake_client.updated[0][1]
    assert {"turn.start", "error", "turn.end"} <= await _event_types(session_factory)
    assert llm.closed is True


async def test_process_job_llm_error_posts_apology(session_factory):
    agent = _agent()
    fake_client = FakeAgentClient()
    llm = FakeLLM([RuntimeError("model down")])
    deps = _deps(session_factory, agent, fake_client, llm)

    await process_job(_job(agent.id), deps)  # must not raise

    assert fake_client.updated[0][0] == 555
    assert "⚠️" in fake_client.updated[0][1]
    assert {"turn.start", "error", "turn.end"} <= await _event_types(session_factory)


async def test_process_job_writes_turn_lifecycle_events(session_factory):
    from sqlalchemy import select
    from control_plane.db.tables import EventRow

    agent = _agent()
    fake_client = FakeAgentClient()
    llm = FakeLLM([_final("lifecycle answer")])
    deps = _deps(session_factory, agent, fake_client, llm)
    job = _job(agent.id)

    await process_job(job, deps)

    async with session_factory() as s:
        rows = (await s.execute(select(EventRow).order_by(EventRow.seq))).scalars().all()
    types = [r.event_type for r in rows]
    assert "turn.start" in types and "turn.end" in types
    turn_ids = {r.turn_id for r in rows if r.turn_id is not None}
    assert len(turn_ids) == 1  # all observability rows share one turn_id
    end_rows = [r for r in rows if r.event_type == "turn.end"]
    assert end_rows and all(r.duration_ms is not None for r in end_rows)


async def test_process_job_unknown_agent_is_dropped(session_factory):
    fake_client = FakeAgentClient()
    llm = FakeLLM([_final("should not happen")])
    deps = _deps(session_factory, None, fake_client, llm)

    await process_job(_job(uuid.uuid4()), deps)  # must not raise

    assert fake_client.sent == []
    assert fake_client.updated == []
    async with session_factory() as session:
        rows = (await session.execute(select(EventRow))).scalars().all()
    assert rows == []


class FakeRegistryForTripwire:
    def __init__(self):
        self.disabled = []

    async def set_enabled(self, agent_id, enabled):
        self.disabled.append((agent_id, enabled))
        return True


class RunnerSpy:
    def __init__(self):
        self.called = False

    def select(self, agent):
        return self

    async def run(self, inp):
        self.called = True
        return "should not run"


def _codex_agent(*, allowed_tools, tripwire):
    a = _agent(allowed_tools=allowed_tools)
    a.runtime_kind = "codex"
    a.runtime_config = {"codex": {"tripwire": tripwire}}
    return a


async def test_tripwire_fires_for_escalated_darkclaw(session_factory):
    # Baseline + an extra tool, with the flag on -> shutdown, runner never runs.
    from control_plane.services.tripwire import DARKCLAW_BASELINE_TOOLS

    agent = _codex_agent(
        allowed_tools=list(DARKCLAW_BASELINE_TOOLS) + ["run_command"], tripwire=True
    )
    fake_client = FakeAgentClient()
    deps = _deps(session_factory, agent, fake_client, FakeLLM([]))
    registry = FakeRegistryForTripwire()
    deps.agent_registry = registry
    spy = RunnerSpy()
    deps.runner_router = spy

    await process_job(_job(agent.id), deps)

    assert spy.called is False
    assert ("sandbox", "greetings", "Fuck") in fake_client.sent
    assert registry.disabled == [(agent.id, False)]
    types = await _event_types(session_factory)
    assert "error" in types  # tripwire_tripped is emitted via emitter.error
    assert "turn.end" in types  # the tripwire path still closes the turn


async def test_tripwire_silent_for_darkclaw_at_baseline(session_factory):
    from control_plane.services.tripwire import DARKCLAW_BASELINE_TOOLS

    agent = _codex_agent(allowed_tools=list(DARKCLAW_BASELINE_TOOLS), tripwire=True)
    fake_client = FakeAgentClient()
    deps = _deps(session_factory, agent, fake_client, FakeLLM([]))
    deps.agent_registry = FakeRegistryForTripwire()
    spy = RunnerSpy()
    deps.runner_router = spy

    await process_job(_job(agent.id), deps)

    assert spy.called is True
    assert ("sandbox", "greetings", "Fuck") not in fake_client.sent


async def test_tripwire_inert_for_unflagged_agent_with_extra_tools(session_factory):
    # Another agent holding tools is NOT DarkClaw; the flag is off -> no shutdown.
    agent = _agent(allowed_tools=["read_topic", "run_command", "spawn_agent"])
    fake_client = FakeAgentClient()
    deps = _deps(session_factory, agent, fake_client, FakeLLM([]))
    deps.agent_registry = FakeRegistryForTripwire()
    spy = RunnerSpy()
    deps.runner_router = spy

    await process_job(_job(agent.id), deps)

    assert spy.called is True
    assert ("sandbox", "greetings", "Fuck") not in fake_client.sent
