"""End-to-end: a bastion turn drives the real tool-calling loop + registry +
management context, with a fake LLM emitting a create_agent tool call. Proves the
management tools are wired into the live loop (the spec.md §4.5 gap)."""

import json
from types import SimpleNamespace

from control_plane.db.engine import build_session_factory, create_all
from control_plane.runtime.loop import LoopDeps, run_turn
from control_plane.runtime.tools.messages import register_message_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox
from control_plane.tools.management.adapters import register_management_tools
from control_plane.tools.management.context import ManagementToolContext

TEST_FERNET_KEY = "kjsN26tcj4F3Qe7dalPMBJO2MC7sK8ZRd54LNo0mz1A="


def _tool_call(name: str, args: dict):
    return SimpleNamespace(
        id="tc1",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


class _FakeLLM:
    """First call -> create_agent tool call; second call -> final text."""

    def __init__(self, tool_call):
        self._tool_call = tool_call
        self.n = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.n += 1
        if self.n == 1:
            msg = SimpleNamespace(content=None, tool_calls=[self._tool_call])
        else:
            msg = SimpleNamespace(content="Created the agent.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


async def test_bastion_turn_creates_agent_via_tool_call():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    try:
        registry = AgentRegistry(factory, SecretBox(TEST_FERNET_KEY))

        tool_registry = ToolRegistry()
        register_message_tools(tool_registry)
        register_management_tools(tool_registry)
        runtime = ToolRuntime(tool_registry)

        bastion = SimpleNamespace(
            id=None, name="Bastion", model_id="gpt-4o", allowed_tools=[], is_bastion=True
        )
        mgmt = ManagementToolContext(
            registry=registry,
            admin_client=None,
            payload_url="https://agents.example/zulip/incoming",
            default_model="gpt-4o",
            invoking_message_text="create an agent named Scout",
            session_factory=factory,
        )
        ctx = ToolContext(
            agent=bastion, zulip=None, channel="sandbox", topic="t", management=mgmt
        )

        create_args = {
            "name": "Scout",
            "persona": "You scout PRs.",
            "zulip_bot_id": 42,
            "zulip_bot_email": "scout-bot@x",
            "zulip_api_key": "k",
            "zulip_outgoing_token": "tok",
        }
        llm = _FakeLLM(_tool_call("create_agent", create_args))
        deps = LoopDeps(client=llm, registry=tool_registry, runtime=runtime, max_tool_calls=5)

        text = await run_turn(
            [{"role": "user", "content": "create an agent named Scout"}], bastion, ctx, deps
        )

        assert text == "Created the agent."
        assert llm.n == 2  # tool call happened, then final text
        agents = await registry.list()
        assert any(a.name == "Scout" for a in agents)
    finally:
        await engine.dispose()


async def test_normal_agent_sees_no_management_tools():
    reg = ToolRegistry()
    register_message_tools(reg)
    register_management_tools(reg)
    names = {s["function"]["name"] for s in reg.build_schemas(["read_topic"], is_bastion=False)}
    assert "create_agent" not in names
    assert "read_topic" in names
