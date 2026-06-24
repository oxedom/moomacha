import uuid

from pydantic import BaseModel

from control_plane.runtime.runners.base import RunnerInput
from control_plane.runtime.runners.deepagents_runner import DeepAgentRunner
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult, ToolRuntime
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.job_queue import Job


class _Args(BaseModel):
    q: str


async def _adapter(parsed, ctx):
    return ToolResult(ok=True, content="ok")


class _FakeGraph:
    def __init__(self):
        self.invoked_with = None

    async def ainvoke(self, state, config=None):
        self.invoked_with = (state, config)

        class _M:
            content = "DEEP FINAL"
        return {"messages": [_M()]}


def _agent():
    return ResolvedAgent(
        id=uuid.uuid4(), name="claw", persona="p", model_id="gpt-4o",
        zulip_bot_email="c@x", zulip_api_key="k", zulip_outgoing_token="t",
        context_message_count=20, readable_channels=["sandbox"],
        allowed_tools=["read_topic"], runtime_kind="deepagents",
        runtime_config={"deepagents": {"skills": ["/skills/personal-assistant/"],
                                       "subagents": ["researcher"]}},
    )


async def test_deepagent_runner_builds_and_returns_final_text():
    reg = ToolRegistry()
    reg.register("read_topic", "Read", _Args, _adapter)
    agent = _agent()
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _FakeGraph()

    runner = DeepAgentRunner(reg, ToolRuntime(reg), build_agent=fake_build)
    ctx = ToolContext(agent=agent, zulip=object(), channel="sandbox", topic="t")
    job = Job(agent_id=agent.id, channel="sandbox", topic="t", content="plan my day")
    inp = RunnerInput(job=job, agent=agent, system_prompt="sys",
                      user_message="plan my day", tool_context=ctx)

    out = await runner.run(inp)

    assert out == "DEEP FINAL"
    assert captured["model"] == "openai:gpt-4o"
    assert captured["system_prompt"] == "sys"
    assert [t.name for t in captured["tools"]] == ["read_topic"]
    assert {s["name"] for s in captured["subagents"]} == {"researcher"}
    assert captured["skills"] == ["/skills/personal-assistant/"]


class _FakeGraphBlocks:
    async def ainvoke(self, state, config=None):
        class _M:
            content = [
                {"type": "text", "text": "Line 1.\n"},
                {"type": "text", "text": "Line 2."},
            ]
        return {"messages": [_M()]}


async def test_deepagent_runner_flattens_content_block_list():
    # Regression: real OpenAI/LangChain returns .content as a block LIST, not a
    # string; the runner must flatten it or Zulip shows a raw dict. Caught by the
    # 2026-05-25 live e2e.
    reg = ToolRegistry()
    reg.register("read_topic", "Read", _Args, _adapter)
    agent = _agent()
    runner = DeepAgentRunner(reg, ToolRuntime(reg), build_agent=lambda **k: _FakeGraphBlocks())
    ctx = ToolContext(agent=agent, zulip=object(), channel="sandbox", topic="t")
    job = Job(agent_id=agent.id, channel="sandbox", topic="t", content="summarize")
    inp = RunnerInput(job=job, agent=agent, system_prompt="sys",
                      user_message="summarize", tool_context=ctx)

    out = await runner.run(inp)

    assert out == "Line 1.\nLine 2."


async def test_thread_id_passed_in_config():
    reg = ToolRegistry()
    reg.register("read_topic", "Read", _Args, _adapter)
    agent = _agent()
    graph = _FakeGraph()
    runner = DeepAgentRunner(reg, ToolRuntime(reg), build_agent=lambda **k: graph)
    ctx = ToolContext(agent=agent, zulip=object(), channel="sandbox", topic="t")
    job = Job(agent_id=agent.id, channel="sandbox", topic="daily", content="hi")
    await runner.run(RunnerInput(job=job, agent=agent, system_prompt="s",
                                 user_message="hi", tool_context=ctx))
    _state, config = graph.invoked_with
    assert config["configurable"]["thread_id"] == f"zulip:stream:sandbox:daily:{agent.id}"
