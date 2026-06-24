from types import SimpleNamespace

from control_plane.runtime.tools.agent_memory import (
    WORKSPACE_NS,
    channel_ns,
    resolve_scope,
    topic_ns,
)
from control_plane.runtime.tools.runtime import ToolContext


def _ctx(memory_ns="agent:archetype:researcher", channel="sandbox", topic="Project X"):
    return ToolContext(
        agent=SimpleNamespace(id="agent-7", is_librarian=False),
        zulip=None,
        channel=channel,
        topic=topic,
        memory_ns=memory_ns,
    )


def test_namespace_string_builders():
    assert channel_ns("sandbox") == "channel:sandbox"
    assert topic_ns("sandbox", "Project X") == "topic:sandbox/Project X"
    assert WORKSPACE_NS == "workspace"


def test_resolve_scope_self_uses_memory_ns():
    ns, tier = resolve_scope("self", _ctx())
    assert ns == "agent:archetype:researcher" and tier == "self"


def test_resolve_scope_self_falls_back_to_agent_id():
    ns, tier = resolve_scope("self", _ctx(memory_ns=None))
    assert ns == "agent:agent-7" and tier == "self"


def test_resolve_scope_topic_channel_workspace():
    assert resolve_scope("topic", _ctx()) == ("topic:sandbox/Project X", "topic")
    assert resolve_scope("channel", _ctx()) == ("channel:sandbox", "channel")
    assert resolve_scope("workspace", _ctx()) == ("workspace", "workspace")


def test_resolve_scope_unknown_returns_none():
    assert resolve_scope("bogus", _ctx()) == (None, None)


import pytest

from control_plane.runtime.tools.agent_memory import AgentMemoryRest


class _CapturingClient:
    """Stands in for httpx.AsyncClient; records the last POST body."""

    def __init__(self):
        self.last = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json, headers):
        self.last = (url, json)

        class _R:
            def raise_for_status(self_inner):
                return None

            def json(self_inner):
                return {"items": []}

        return _R()


@pytest.fixture
def rest(monkeypatch):
    captured = _CapturingClient()
    monkeypatch.setattr(
        "control_plane.runtime.tools.agent_memory.httpx.AsyncClient",
        lambda *a, **k: captured,
    )
    client = AgentMemoryRest(endpoint="http://x", store_id="s", api_key="k")
    client._captured = captured  # type: ignore[attr-defined]
    return client


async def test_create_memories_sends_namespace_and_type(rest):
    await rest.create_memories(
        [{"id": "1", "text": "fact", "namespace": "channel:sandbox", "memoryType": "semantic"}]
    )
    _, body = rest._captured.last
    mem = body["memories"][0]
    assert mem["namespace"] == "channel:sandbox"
    assert mem["memoryType"] == "semantic"


async def test_search_sends_namespace_filter(rest):
    await rest.search_memories("hi", limit=3, namespaces=["agent:x", "channel:sandbox"])
    _, body = rest._captured.last
    assert body["text"] == "hi"
    assert body["limit"] == 3
    assert body["namespace"] == ["agent:x", "channel:sandbox"]


from control_plane.runtime.tools.agent_memory import register_agent_memory_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime


class FakeRest:
    def __init__(self):
        self.created = []
        self._items = []  # search returns these item dicts, like the real store

    async def create_memories(self, memories):
        self.created.append(memories)
        return "ok"

    async def search_memories(self, text, limit=5, namespaces=None):
        self.created.append(("search", text, namespaces))
        return self._items

    async def add_session_event(self, session_id, text, role="USER"):
        return "ok"

    async def get_session(self, session_id):
        return "ok"


def _agent(is_librarian=False):
    return SimpleNamespace(
        id="agent-7",
        is_librarian=is_librarian,
        allowed_tools=["remember", "record_episode", "search_long_term_memory"],
    )


def _rt(rest):
    reg = ToolRegistry()
    register_agent_memory_tools(reg, rest)
    return ToolRuntime(reg)


def _mctx(agent, memory_ns="agent:archetype:researcher"):
    return ToolContext(agent=agent, zulip=None, channel="sandbox", topic="T", memory_ns=memory_ns)


async def test_remember_default_self_scope_sets_namespace():
    rest = FakeRest()
    res = await _rt(rest).execute("remember", '{"text": "x"}', _mctx(_agent()))
    assert res.ok
    mem = rest.created[0][0]
    assert mem["namespace"] == "agent-archetype-researcher"  # wire-encoded
    assert mem["memoryType"] == "semantic"


async def test_remember_topic_scope_allowed_for_agent():
    rest = FakeRest()
    res = await _rt(rest).execute("remember", '{"text": "x", "scope": "topic"}', _mctx(_agent()))
    assert res.ok
    assert rest.created[0][0]["namespace"] == "topic-sandbox-T"  # wire-encoded


async def test_remember_channel_scope_rejected_for_non_librarian():
    rest = FakeRest()
    res = await _rt(rest).execute("remember", '{"text": "x", "scope": "channel"}', _mctx(_agent()))
    assert res.ok is False
    assert "librarian" in res.content.lower()
    assert rest.created == []


async def test_remember_channel_scope_allowed_for_librarian():
    rest = FakeRest()
    res = await _rt(rest).execute(
        "remember", '{"text": "x", "scope": "channel"}', _mctx(_agent(is_librarian=True))
    )
    assert res.ok
    assert rest.created[0][0]["namespace"] == "channel-sandbox"  # wire-encoded


async def test_record_episode_sets_episodic_type_and_event_date():
    rest = FakeRest()
    res = await _rt(rest).execute(
        "record_episode",
        '{"text": "deploy shipped", "event_date": "2026-05-25"}',
        _mctx(_agent()),
    )
    assert res.ok
    mem = rest.created[0][0]
    assert mem["memoryType"] == "episodic"
    assert mem["event_date"] == "2026-05-25"
    assert mem["namespace"] == "agent-archetype-researcher"  # wire-encoded


async def test_record_episode_channel_scope_rejected_for_non_librarian():
    rest = FakeRest()
    res = await _rt(rest).execute(
        "record_episode", '{"text": "x", "scope": "channel"}', _mctx(_agent())
    )
    assert res.ok is False and rest.created == []


async def test_search_filters_to_self_plus_channel_read_set():
    rest = FakeRest()
    res = await _rt(rest).execute(
        "search_long_term_memory", '{"query": "deploy", "limit": 4}', _mctx(_agent())
    )
    assert res.ok
    tag, text, namespaces = rest.created[0]
    assert tag == "search" and text == "deploy"
    assert namespaces == ["agent-archetype-researcher", "channel-sandbox"]  # wire-encoded


def test_wire_ns_encodes_to_store_charset():
    from control_plane.runtime.tools.agent_memory import wire_ns
    # store accepts only [A-Za-z0-9-]; colons, slashes, spaces collapse to hyphens
    assert wire_ns("agent:archetype:researcher") == "agent-archetype-researcher"
    assert wire_ns("topic:sandbox/Project X") == "topic-sandbox-Project-X"
    assert wire_ns("channel:sandbox") == "channel-sandbox"
    assert wire_ns("workspace") == "workspace"
    # never returns empty
    assert wire_ns(":::") == "ns"


async def test_search_filters_returned_items_to_read_set():
    rest = FakeRest()
    # the store returns hits across namespaces; only the agent's read-set survives
    rest._items = [
        {"text": "mine", "namespace": "agent-archetype-researcher"},
        {"text": "this channel", "namespace": "channel-sandbox"},
        {"text": "someone else", "namespace": "agent-archetype-other"},
        {"text": "another topic", "namespace": "topic-sandbox-OtherTopic"},
    ]
    res = await _rt(rest).execute("search_long_term_memory", '{"query": "x"}', _mctx(_agent()))
    assert res.ok
    assert "mine" in res.content and "this channel" in res.content
    assert "someone else" not in res.content and "another topic" not in res.content
