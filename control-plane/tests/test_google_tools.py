"""Network-free tests for the Google Calendar + Tasks tools.

FakeGoogleClient records (method, url, params, json) and returns canned bodies,
so adapters are exercised without touching Google. The GoogleClient's own
token-refresh / 401-retry logic is tested separately by stubbing ``_send``.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from control_plane.runtime.tools.gcal import register_gcal_tools
from control_plane.runtime.tools.google_api import GoogleApiError, GoogleClient, _UNAUTHORIZED
from control_plane.runtime.tools.gtasks import register_gtasks_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime

GCAL_TOOLS = [
    "gcal_list_calendars",
    "gcal_list_events",
    "gcal_search_events",
    "gcal_get_event",
    "gcal_create_event",
]
GTASKS_TOOLS = [
    "gtasks_list_task_lists",
    "gtasks_list_tasks",
    "gtasks_create_task",
    "gtasks_complete_task",
    "gtasks_update_task",
    "gtasks_delete_task",
]
ALL_TOOLS = GCAL_TOOLS + GTASKS_TOOLS


class FakeGoogleClient:
    def __init__(self, reply: Any = None, raises: Exception | None = None) -> None:
        self.reply = reply if reply is not None else {}
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    async def request(self, method, url, *, params=None, json=None):
        self.calls.append({"method": method, "url": url, "params": params or {}, "json": json})
        if self.raises:
            raise self.raises
        return self.reply


@dataclass
class FakeAgent:
    id: str = "agent-1"
    allowed_tools: list[str] = field(default_factory=lambda: list(ALL_TOOLS))
    is_bastion: bool = False


def _ctx() -> ToolContext:
    return ToolContext(agent=FakeAgent(), zulip=None, channel="sandbox", topic="claw")


def _registry(client) -> ToolRegistry:
    reg = ToolRegistry()
    register_gcal_tools(reg, client)
    register_gtasks_tools(reg, client)
    return reg


# --- calendar -----------------------------------------------------------------


async def test_list_calendars_formats_primary():
    client = FakeGoogleClient(
        {"items": [{"summary": "Work", "id": "w@x", "primary": True}, {"summary": "Fun", "id": "f@x"}]}
    )
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute("gcal_list_calendars", "{}", _ctx())
    assert res.ok
    assert "Work (primary)" in res.content
    assert "id=f@x" in res.content


async def test_list_events_defaults_window_and_orders():
    client = FakeGoogleClient(
        {"items": [{"summary": "Standup", "start": {"dateTime": "2026-06-04T09:00:00Z"}, "end": {"dateTime": "2026-06-04T09:15:00Z"}}]}
    )
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute("gcal_list_events", "{}", _ctx())
    assert res.ok
    call = client.calls[0]
    assert call["url"].endswith("/calendars/primary/events")
    assert call["params"]["singleEvents"] == "true"
    assert call["params"]["orderBy"] == "startTime"
    assert "timeMin" in call["params"] and "timeMax" in call["params"]
    assert "Standup" in res.content


async def test_create_event_timed_includes_timezone():
    client = FakeGoogleClient({"summary": "Meeting", "start": {"dateTime": "2026-06-04T10:00:00-05:00"}, "end": {"dateTime": "2026-06-04T11:00:00-05:00"}, "htmlLink": "http://x"})
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute(
        "gcal_create_event",
        '{"summary":"Meeting","start":"2026-06-04T10:00:00-05:00","end":"2026-06-04T11:00:00-05:00","time_zone":"America/New_York"}',
        _ctx(),
    )
    assert res.ok
    body = client.calls[0]["json"]
    assert body["summary"] == "Meeting"
    assert body["start"] == {"dateTime": "2026-06-04T10:00:00-05:00", "timeZone": "America/New_York"}


async def test_create_event_all_day_uses_date_node():
    client = FakeGoogleClient({"summary": "Holiday", "start": {"date": "2026-06-04"}, "end": {"date": "2026-06-05"}})
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute(
        "gcal_create_event",
        '{"summary":"Holiday","start":"2026-06-04","end":"2026-06-05"}',
        _ctx(),
    )
    assert res.ok
    body = client.calls[0]["json"]
    assert body["start"] == {"date": "2026-06-04"}
    assert "timeZone" not in body["start"]


async def test_search_events_forwards_query():
    client = FakeGoogleClient({"items": []})
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute("gcal_search_events", '{"query":"dentist"}', _ctx())
    assert res.ok
    assert client.calls[0]["params"]["q"] == "dentist"
    assert "no events matching 'dentist'" in res.content


# --- tasks --------------------------------------------------------------------


async def test_list_task_lists_formats():
    client = FakeGoogleClient({"items": [{"title": "My Tasks", "id": "abc"}, {"title": "Career", "id": "def"}]})
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute("gtasks_list_task_lists", "{}", _ctx())
    assert res.ok
    assert "My Tasks" in res.content and "id=def" in res.content


async def test_list_tasks_incomplete_by_default():
    client = FakeGoogleClient({"items": [{"title": "Ship it", "status": "needsAction", "id": "t1"}]})
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute("gtasks_list_tasks", '{"list_id":"abc"}', _ctx())
    assert res.ok
    assert client.calls[0]["params"]["showCompleted"] == "false"
    assert "[ ] Ship it" in res.content


async def test_complete_task_patches_status():
    client = FakeGoogleClient({"title": "Ship it", "status": "completed", "id": "t1"})
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute("gtasks_complete_task", '{"list_id":"abc","task_id":"t1"}', _ctx())
    assert res.ok
    assert client.calls[0]["method"] == "PATCH"
    assert client.calls[0]["json"] == {"status": "completed"}
    assert "[x] Ship it" in res.content


async def test_create_task_builds_body():
    client = FakeGoogleClient({"title": "Call bank", "status": "needsAction", "id": "t2", "due": "2026-06-10T00:00:00Z"})
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute(
        "gtasks_create_task",
        '{"list_id":"abc","title":"Call bank","due":"2026-06-10T00:00:00Z"}',
        _ctx(),
    )
    assert res.ok
    assert client.calls[0]["json"] == {"title": "Call bank", "due": "2026-06-10T00:00:00Z"}


async def test_update_task_requires_a_field():
    client = FakeGoogleClient({})
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute("gtasks_update_task", '{"list_id":"abc","task_id":"t1"}', _ctx())
    assert res.ok is False
    assert client.calls == []  # nothing sent


async def test_delete_task():
    client = FakeGoogleClient({})
    runtime = ToolRuntime(_registry(client))
    res = await runtime.execute("gtasks_delete_task", '{"list_id":"abc","task_id":"t1"}', _ctx())
    assert res.ok
    assert client.calls[0]["method"] == "DELETE"


# --- registration -------------------------------------------------------------


def test_all_tools_registered_with_schemas():
    reg = _registry(FakeGoogleClient())
    names = {s["function"]["name"] for s in reg.build_schemas(ALL_TOOLS)}
    assert names == set(ALL_TOOLS)


def test_tools_absent_when_not_registered():
    reg = ToolRegistry()
    assert reg.get("gcal_list_events") is None


# --- GoogleClient token logic -------------------------------------------------


async def test_client_refreshes_then_caches_token(monkeypatch):
    client = GoogleClient(client_id="c", client_secret="s", refresh_token="r")
    refreshes = {"n": 0}

    async def fake_refresh():
        refreshes["n"] += 1
        client._access_token = "tok"
        client._expires_at = 1e18  # far future

    sends: list[str] = []

    async def fake_send(method, url, token, params, json):
        sends.append(token)
        return {"ok": True}

    monkeypatch.setattr(client, "_refresh", fake_refresh)
    monkeypatch.setattr(client, "_send", fake_send)

    await client.request("GET", "http://x")
    await client.request("GET", "http://x")
    assert refreshes["n"] == 1  # second call reused the cached token
    assert sends == ["tok", "tok"]


async def test_client_retries_once_on_401(monkeypatch):
    client = GoogleClient(client_id="c", client_secret="s", refresh_token="r")
    tokens = iter(["stale", "fresh"])

    async def fake_refresh():
        client._access_token = next(tokens)
        client._expires_at = 1e18

    seen: list[str] = []

    async def fake_send(method, url, token, params, json):
        seen.append(token)
        if token == "stale":
            return _UNAUTHORIZED
        return {"ok": True}

    monkeypatch.setattr(client, "_refresh", fake_refresh)
    monkeypatch.setattr(client, "_send", fake_send)

    body = await client.request("GET", "http://x")
    assert body == {"ok": True}
    assert seen == ["stale", "fresh"]


async def test_client_raises_when_401_persists(monkeypatch):
    client = GoogleClient(client_id="c", client_secret="s", refresh_token="r")

    async def fake_refresh():
        client._access_token = "tok"
        client._expires_at = 1e18

    async def fake_send(method, url, token, params, json):
        return _UNAUTHORIZED

    monkeypatch.setattr(client, "_refresh", fake_refresh)
    monkeypatch.setattr(client, "_send", fake_send)

    with pytest.raises(GoogleApiError):
        await client.request("GET", "http://x")
