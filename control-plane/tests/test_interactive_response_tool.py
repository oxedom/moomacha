import uuid
from datetime import UTC, datetime

from control_plane.db.engine import build_session_factory, create_all
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext
from control_plane.runtime.tools.interactive_response import register_interactive_response_tools
from control_plane.services.artifact_store import ArtifactStore


def _now():
    return datetime(2026, 5, 26, 12, 0, tzinfo=UTC)


class _FakeZulip:
    def __init__(self):
        self.sent = []

    async def send_message(self, channel, topic, content):
        self.sent.append((channel, topic, content))
        return 321


class _Agent:
    id = uuid.uuid4()
    name = "Claw"
    allowed_tools = ["create_interactive_response"]
    is_bastion = False
    can_exec = False


async def _setup():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    events = []

    async def write_event(**kwargs):
        events.append(kwargs)

    store = ArtifactStore(factory, write_event, clock=_now)
    reg = ToolRegistry()
    register_interactive_response_tools(
        reg, store,
        base_url="https://app.test",
        default_expiry_minutes=2880,
        max_expiry_minutes=20160,
        max_html_bytes=524288,
        clock=_now,
    )
    return reg, store, factory, engine, events


async def test_tool_creates_artifact_posts_link_and_returns_url():
    reg, store, factory, engine, events = await _setup()
    try:
        entry = reg.get("create_interactive_response")
        zulip = _FakeZulip()
        ctx = ToolContext(
            agent=_Agent(), zulip=zulip, channel="sandbox", topic="Deploy approval",
            source_message_id=99,
        )
        inp = entry.input_model.model_validate({
            "title": "Deploy approval",
            "html": "<!doctype html><html><head></head><body>form</body></html>",
            "expires_in_minutes": 60,
        })
        result = await entry.adapter(inp, ctx)
        assert result.ok
        assert "/ui/artifacts/" in result.content
        assert len(zulip.sent) == 1
        assert any(e["event_type"] == "interactive_artifact_posted" for e in events)
    finally:
        await engine.dispose()


async def test_tool_clamps_over_cap_expiry():
    reg, store, factory, engine, _events = await _setup()
    try:
        entry = reg.get("create_interactive_response")
        ctx = ToolContext(agent=_Agent(), zulip=_FakeZulip(), channel="c", topic="t",
                          source_message_id=None)
        inp = entry.input_model.model_validate({
            "title": "x", "html": "<html></html>", "expires_in_minutes": 999999,
        })
        result = await entry.adapter(inp, ctx)
        assert result.ok
        from control_plane.db.tables import InteractiveArtifactRow
        from sqlalchemy import select
        async with factory() as s:
            row = (await s.execute(select(InteractiveArtifactRow))).scalars().first()
        # clamped to the 14-day cap (20160 minutes)
        assert abs((row.expires_at - _now()).total_seconds() - 20160 * 60) < 2
    finally:
        await engine.dispose()


async def test_tool_rejects_oversize_html():
    reg, _store, _factory, engine, _events = await _setup()
    try:
        entry = reg.get("create_interactive_response")
        ctx = ToolContext(agent=_Agent(), zulip=_FakeZulip(), channel="c", topic="t",
                          source_message_id=None)
        inp = entry.input_model.model_validate({
            "title": "x", "html": "<html>" + "z" * 600000 + "</html>",
        })
        result = await entry.adapter(inp, ctx)
        assert not result.ok
        assert "too large" in result.content.lower()
    finally:
        await engine.dispose()


async def test_tool_refuses_in_direct_message_without_creating_or_posting():
    reg, store, factory, engine, events = await _setup()
    try:
        entry = reg.get("create_interactive_response")
        zulip = _FakeZulip()
        # DM context: the webhook sets channel="direct"/topic="" + conversation_type="direct".
        ctx = ToolContext(
            agent=_Agent(), zulip=zulip, channel="direct", topic="",
            source_message_id=5, conversation_type="direct",
        )
        inp = entry.input_model.model_validate(
            {"title": "x", "html": "<html></html>", "expires_in_minutes": 60}
        )
        result = await entry.adapter(inp, ctx)
        assert not result.ok
        assert "direct message" in result.content.lower()
        # Nothing posted and no artifact persisted.
        assert zulip.sent == []
        from sqlalchemy import select
        from control_plane.db.tables import InteractiveArtifactRow
        async with factory() as s:
            rows = (await s.execute(select(InteractiveArtifactRow))).scalars().all()
        assert rows == []
    finally:
        await engine.dispose()
