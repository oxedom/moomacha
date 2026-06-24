"""Network-free tests for the REST-backed agent memory tools.

A fake AgentMemoryRest records calls so we can assert the adapters build the
right payloads without opening any network connections.
"""

from dataclasses import dataclass, field

from control_plane.runtime.tools.agent_memory import (
    AgentMemoryRest,
    register_agent_memory_tools,
    session_id_for,
)
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime


class FakeRest:
    """Records method calls instead of hitting the network."""

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.calls: list[tuple[str, tuple, dict]] = []
        self.search_items: list[dict] = []

    async def search_memories(self, text, limit=5, namespaces=None):
        self.calls.append(("search_memories", (text,), {"limit": limit, "namespaces": namespaces}))
        return self.search_items

    async def create_memories(self, memories):
        self.calls.append(("create_memories", (memories,), {}))
        return self.reply

    async def add_session_event(self, session_id, text, role="USER"):
        self.calls.append(("add_session_event", (session_id, text), {"role": role}))
        return self.reply

    async def get_session(self, session_id):
        self.calls.append(("get_session", (session_id,), {}))
        return self.reply


@dataclass
class FakeAgent:
    id: str = "agent-7"
    allowed_tools: list[str] = field(
        default_factory=lambda: [
            "search_long_term_memory",
            "remember",
            "set_working_memory",
            "get_working_memory",
        ]
    )
    is_bastion: bool = False


def _ctx(channel="sandbox", topic="Project X") -> ToolContext:
    return ToolContext(agent=FakeAgent(), zulip=None, channel=channel, topic=topic)


def _registry(rest) -> ToolRegistry:
    reg = ToolRegistry()
    register_agent_memory_tools(reg, rest)
    return reg


def test_session_id_for_slugifies_channel_and_topic():
    # The Agent Memory service only accepts alphanumerics + hyphens in IDs.
    assert session_id_for("sandbox", "Project X") == "sandbox-Project-X"
    assert session_id_for("sandbox", "a/b.c_d") == "sandbox-a-b-c-d"
    assert session_id_for("sandbox", "  ") == "sandbox"


async def test_search_calls_search_memories():
    fake = FakeRest()
    # _ctx() agent id "agent-7" (no memory_ns) -> read-set self tier "agent-agent-7"
    fake.search_items = [{"text": "fact one", "namespace": "agent-agent-7"}]
    runtime = ToolRuntime(_registry(fake))

    result = await runtime.execute(
        "search_long_term_memory", '{"query": "deploy code", "limit": 3}', _ctx()
    )

    assert result.ok and "fact one" in result.content
    method, args, kwargs = fake.calls[0]
    assert method == "search_memories"
    assert args[0] == "deploy code"
    # _search over-fetches (managed store does not filter by namespace) then trims client-side
    assert kwargs["limit"] >= 3
    assert kwargs["namespaces"]  # read-set passed


async def test_remember_stores_single_memory():
    fake = FakeRest()
    runtime = ToolRuntime(_registry(fake))

    result = await runtime.execute("remember", '{"text": "deploy code is 1234"}', _ctx())

    assert result.ok
    method, args, _ = fake.calls[0]
    assert method == "create_memories"
    memories = args[0]
    assert len(memories) == 1
    assert memories[0]["text"] == "deploy code is 1234"
    assert "id" in memories[0]  # uuid assigned by adapter


async def test_set_working_memory_uses_topic_session():
    fake = FakeRest()
    runtime = ToolRuntime(_registry(fake))

    await runtime.execute("set_working_memory", '{"data": "draft notes"}', _ctx())

    method, args, kwargs = fake.calls[0]
    assert method == "add_session_event"
    assert args[0] == "sandbox-Project-X"
    assert args[1] == "draft notes"


async def test_get_working_memory_uses_topic_session():
    fake = FakeRest(reply="{'messages': []}")
    runtime = ToolRuntime(_registry(fake))

    result = await runtime.execute("get_working_memory", "{}", _ctx())

    assert result.ok
    method, args, _ = fake.calls[0]
    assert method == "get_session"
    assert args[0] == "sandbox-Project-X"


async def test_search_passes_namespace_read_set():
    """Long-term search is scoped to the agent's read-set (self + channel), not org-wide."""
    fake = FakeRest()
    runtime = ToolRuntime(_registry(fake))
    await runtime.execute("search_long_term_memory", '{"query": "x"}', _ctx())
    method, args, kwargs = fake.calls[0]
    assert method == "search_memories"
    assert args[0] == "x"
    assert "namespaces" in kwargs and kwargs["namespaces"]  # a non-empty read-set was passed


def test_schemas_exclude_internal_fields():
    reg = _registry(FakeRest())
    schemas = {
        s["function"]["name"]: s["function"]["parameters"]
        for s in reg.build_schemas(
            ["search_long_term_memory", "remember", "set_working_memory", "get_working_memory"]
        )
    }
    assert set(schemas) == {
        "search_long_term_memory",
        "remember",
        "set_working_memory",
        "get_working_memory",
    }
    # session_id is never exposed to the model
    assert "session_id" not in schemas["set_working_memory"]["properties"]


def test_tools_absent_when_not_registered():
    reg = ToolRegistry()
    assert reg.get("remember") is None
    assert reg.build_schemas(["remember", "search_long_term_memory"]) == []


def test_agent_memory_rest_builds_correct_base_url():
    rest = AgentMemoryRest(
        endpoint="https://gcp-us-east4.memory.redis.io",
        store_id="abc123",
        api_key="key",
    )
    assert rest._base == "https://gcp-us-east4.memory.redis.io/v1/stores/abc123"
    assert rest._headers["Authorization"] == "Bearer key"


def test_agent_memory_rest_supports_local_server_without_store_or_key():
    rest = AgentMemoryRest(endpoint="http://agent-memory:8000")
    assert rest._base == "http://agent-memory:8000/v1"
    assert "Authorization" not in rest._headers
