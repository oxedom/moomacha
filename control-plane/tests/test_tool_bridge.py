import uuid

from pydantic import BaseModel

from control_plane.runtime.runners.tool_bridge import bridge_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult, ToolRuntime
from control_plane.schemas.agents import ResolvedAgent


class _Args(BaseModel):
    q: str


async def _adapter(parsed, ctx):
    return ToolResult(ok=True, content=f"searched:{parsed.q}")


def _registry():
    reg = ToolRegistry()
    reg.register("read_topic", "Read a topic", _Args, _adapter)
    reg.register("secret_tool", "Not allowed", _Args, _adapter)
    return reg


def _agent(allowed):
    return ResolvedAgent(
        id=uuid.uuid4(), name="claw", persona="p", model_id="gpt-4o",
        zulip_bot_email="c@x", zulip_api_key="k", zulip_outgoing_token="t",
        context_message_count=20, readable_channels=["sandbox"], allowed_tools=allowed,
    )


async def test_bridge_only_exposes_allowed_tools():
    reg = _registry()
    agent = _agent(["read_topic"])
    ctx = ToolContext(agent=agent, zulip=object(), channel="sandbox", topic="t")
    tools = bridge_tools(reg, agent, ToolRuntime(reg), ctx)
    assert [t.name for t in tools] == ["read_topic"]


async def test_bridged_tool_calls_runtime_execute_and_records_callback():
    reg = _registry()
    agent = _agent(["read_topic"])
    ctx = ToolContext(agent=agent, zulip=object(), channel="sandbox", topic="t")
    seen = []

    async def on_call(name, ok):
        seen.append((name, ok))

    tools = bridge_tools(reg, agent, ToolRuntime(reg), ctx, on_tool_call=on_call)
    out = await tools[0].ainvoke({"q": "neon"})
    assert out == "searched:neon"
    assert seen == [("read_topic", True)]


async def test_denied_tool_returns_not_permitted_via_runtime():
    reg = _registry()
    runtime = ToolRuntime(reg)
    restricted = _agent(["read_topic"])
    ctx = ToolContext(agent=restricted, zulip=object(), channel="sandbox", topic="t")
    allowed_agent = _agent(["read_topic", "secret_tool"])
    # Bridge using the allowed agent (so secret_tool is built), but ctx carries the
    # restricted agent -> ToolRuntime.execute must deny secret_tool (defense in depth).
    tools = {t.name: t for t in bridge_tools(reg, allowed_agent, runtime, ctx)}
    out = await tools["secret_tool"].ainvoke({"q": "x"})
    assert "not permitted" in out


async def test_deepagents_bridged_tool_emits_event():
    from control_plane.observability.events import AgentEvent, EventEmitter

    captured: list[AgentEvent] = []

    async def sink(ev: AgentEvent) -> None:
        captured.append(ev)

    reg = _registry()
    agent = _agent(["read_topic"])
    emitter = EventEmitter(trace_id="tr", turn_id="tn", emit_fn=sink)
    ctx = ToolContext(agent=agent, zulip=object(), channel="sandbox", topic="t", events=emitter)
    tools = bridge_tools(reg, agent, ToolRuntime(reg), ctx, on_tool_call=None)
    tool = {t.name: t for t in tools}["read_topic"]
    out = await tool.ainvoke({"q": "hello"})
    assert out == "searched:hello"
    assert any(e.type == "tool.call" and e.attrs.get("name") == "read_topic" for e in captured)
