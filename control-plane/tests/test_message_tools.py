from types import SimpleNamespace

from control_plane.runtime.tools.messages import (
    ReadChannelInput,
    ReadTopicInput,
    read_channel,
    read_topic,
    register_message_tools,
)
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext


class FakeZulip:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def get_messages(self, channel, topic, num_before):
        self.calls.append(("topic", channel, topic, num_before))
        return [
            {"sender_full_name": "Ann", "content": "hi"},
            {"sender_full_name": "Bob", "content": "yo"},
        ]

    async def get_channel_messages(self, channel, num_before):
        self.calls.append(("channel", channel, num_before))
        return [{"sender_full_name": "Ann", "content": "hi"}]


def _ctx(zulip, readable) -> ToolContext:
    agent = SimpleNamespace(
        allowed_tools=["read_topic", "read_channel"], readable_channels=readable
    )
    return ToolContext(agent=agent, zulip=zulip, channel="eng", topic="standup")


async def test_read_topic_in_scope_formats_messages():
    z = FakeZulip()
    res = await read_topic(ReadTopicInput(channel="eng", topic="standup", limit=20), _ctx(z, ["eng"]))
    assert res.ok is True
    assert "Ann: hi" in res.content
    assert "Bob: yo" in res.content
    assert z.calls[0][0] == "topic"


async def test_read_topic_out_of_scope_denied_without_fetch():
    z = FakeZulip()
    res = await read_topic(ReadTopicInput(channel="secret", topic="x", limit=20), _ctx(z, ["eng"]))
    assert res.ok is False
    assert "not permitted" in res.content.lower()
    assert z.calls == []  # never touched Zulip


async def test_read_channel_out_of_scope_denied_without_fetch():
    z = FakeZulip()
    res = await read_channel(ReadChannelInput(channel="secret", limit=10), _ctx(z, ["eng"]))
    assert res.ok is False
    assert "not permitted" in res.content.lower()
    assert z.calls == []  # never touched Zulip


async def test_read_channel_wildcard_scope_allows_any():
    z = FakeZulip()
    res = await read_channel(ReadChannelInput(channel="anything", limit=10), _ctx(z, ["*"]))
    assert res.ok is True
    assert "Ann: hi" in res.content
    assert z.calls[0][0] == "channel"


def test_register_message_tools_registers_both():
    reg = ToolRegistry()
    register_message_tools(reg)
    schemas = reg.build_schemas(["read_topic", "read_channel"])
    assert {s["function"]["name"] for s in schemas} == {"read_topic", "read_channel"}
