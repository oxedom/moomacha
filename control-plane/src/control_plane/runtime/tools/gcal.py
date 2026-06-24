"""Google Calendar tools (in-process REST over the Calendar v3 API).

Backed by the shared :class:`GoogleClient` (one OAuth token for Calendar +
Tasks). Adapters return human-readable text so the agent can relay it directly;
the raw API shapes never leak into the chat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from control_plane.runtime.tools.google_api import GoogleClient
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult

_CAL = "https://www.googleapis.com/calendar/v3"
OUTPUT_CAP = 8000


def _cap(value: str, limit: int = OUTPUT_CAP) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated to {limit} characters ..."


def _parse_rfc3339(value: str | None) -> datetime | None:
    """Best-effort parse of an RFC3339 / date string to an aware UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _when(node: dict[str, Any]) -> str:
    """A start/end node is either {dateTime,...} (timed) or {date} (all-day)."""
    return node.get("dateTime") or node.get("date") or "?"


def _fmt_event(ev: dict[str, Any]) -> str:
    start = _when(ev.get("start") or {})
    end = _when(ev.get("end") or {})
    summary = ev.get("summary") or "(no title)"
    loc = ev.get("location")
    line = f"- {start} → {end}: {summary}"
    if loc:
        line += f"  @ {loc}"
    if ev.get("id"):
        line += f"  [id={ev['id']}]"
    return line


# --- input models -------------------------------------------------------------


class ListCalendarsInput(BaseModel):
    pass


class ListEventsInput(BaseModel):
    calendar_id: str = Field(
        default="primary",
        description="Calendar ID, 'primary' for the main calendar, or a calendar's email/id.",
    )
    time_min: str | None = Field(
        default=None,
        description="RFC3339 lower bound (e.g. '2026-06-04T00:00:00Z'). Defaults to now.",
    )
    time_max: str | None = Field(
        default=None,
        description="RFC3339 upper bound. Defaults to 7 days after time_min.",
    )
    max_results: int = Field(default=20, ge=1, le=100, description="Max events to return.")


class SearchEventsInput(BaseModel):
    query: str = Field(description="Free-text search over event fields (title, description, etc.).")
    calendar_id: str = Field(default="primary", description="Calendar ID to search.")
    time_min: str | None = Field(default=None, description="RFC3339 lower bound. Defaults to now.")
    time_max: str | None = Field(default=None, description="RFC3339 upper bound. Defaults to +30 days.")
    max_results: int = Field(default=20, ge=1, le=100)


class GetEventInput(BaseModel):
    event_id: str = Field(description="The event id.")
    calendar_id: str = Field(default="primary", description="Calendar the event lives on.")


class CreateEventInput(BaseModel):
    summary: str = Field(description="Event title.")
    start: str = Field(
        description="Start: RFC3339 datetime '2026-06-04T10:00:00+03:00' (timed) or '2026-06-04' (all-day)."
    )
    end: str = Field(description="End: same format as start.")
    calendar_id: str = Field(default="primary", description="Calendar to create the event on.")
    description: str | None = Field(default=None, description="Event description/notes.")
    location: str | None = Field(default=None, description="Event location.")
    time_zone: str | None = Field(
        default=None, description="IANA time zone (e.g. 'America/New_York') for timed events."
    )


# --- adapters -----------------------------------------------------------------


def _time_node(value: str, time_zone: str | None) -> dict[str, Any]:
    # All-day events use {date}; timed events use {dateTime[, timeZone]}.
    if len(value) == 10 and value.count("-") == 2:
        return {"date": value}
    node: dict[str, Any] = {"dateTime": value}
    if time_zone:
        node["timeZone"] = time_zone
    return node


async def _list_calendars(inp: ListCalendarsInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    data = await g.request("GET", f"{_CAL}/users/me/calendarList", params={"maxResults": 100})
    items = data.get("items", [])
    if not items:
        return ToolResult(ok=True, content="(no calendars)")
    lines = []
    for c in items:
        flag = " (primary)" if c.get("primary") else ""
        lines.append(f"- {c.get('summary')}{flag}  [id={c.get('id')}]")
    return ToolResult(ok=True, content=_cap("\n".join(lines)))


async def _list_events(inp: ListEventsInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    now = datetime.now(UTC)
    time_min = inp.time_min or now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if inp.time_max:
        time_max = inp.time_max
    else:
        # Default window: 7 days from the lower bound (now, unless time_min was given).
        anchor = _parse_rfc3339(inp.time_min) or now
        time_max = (anchor + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = await g.request(
        "GET",
        f"{_CAL}/calendars/{inp.calendar_id}/events",
        params={
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": inp.max_results,
        },
    )
    items = data.get("items", [])
    if not items:
        return ToolResult(ok=True, content=f"(no events between {time_min} and {time_max})")
    header = f"Events on '{inp.calendar_id}' ({time_min} → {time_max}):"
    return ToolResult(ok=True, content=_cap(header + "\n" + "\n".join(_fmt_event(e) for e in items)))


async def _search_events(inp: SearchEventsInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    now = datetime.now(UTC)
    time_min = inp.time_min or now.strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = inp.time_max or (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = await g.request(
        "GET",
        f"{_CAL}/calendars/{inp.calendar_id}/events",
        params={
            "q": inp.query,
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": inp.max_results,
        },
    )
    items = data.get("items", [])
    if not items:
        return ToolResult(ok=True, content=f"(no events matching '{inp.query}')")
    return ToolResult(
        ok=True, content=_cap(f"Matches for '{inp.query}':\n" + "\n".join(_fmt_event(e) for e in items))
    )


async def _get_event(inp: GetEventInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    ev = await g.request("GET", f"{_CAL}/calendars/{inp.calendar_id}/events/{inp.event_id}")
    lines = [
        _fmt_event(ev),
        f"  status: {ev.get('status')}",
    ]
    if ev.get("description"):
        lines.append(f"  notes: {ev['description']}")
    if ev.get("attendees"):
        lines.append("  attendees: " + ", ".join(a.get("email", "?") for a in ev["attendees"]))
    if ev.get("htmlLink"):
        lines.append(f"  link: {ev['htmlLink']}")
    return ToolResult(ok=True, content=_cap("\n".join(lines)))


async def _create_event(inp: CreateEventInput, ctx: ToolContext, g: GoogleClient) -> ToolResult:
    body: dict[str, Any] = {
        "summary": inp.summary,
        "start": _time_node(inp.start, inp.time_zone),
        "end": _time_node(inp.end, inp.time_zone),
    }
    if inp.description:
        body["description"] = inp.description
    if inp.location:
        body["location"] = inp.location
    ev = await g.request("POST", f"{_CAL}/calendars/{inp.calendar_id}/events", json=body)
    return ToolResult(
        ok=True,
        content=f"Created event:\n{_fmt_event(ev)}\nlink: {ev.get('htmlLink', '?')}",
    )


_TOOLS = [
    ("gcal_list_calendars", "List the user's Google calendars (name + id).", ListCalendarsInput, _list_calendars),
    (
        "gcal_list_events",
        "List Google Calendar events in a time window (defaults to the next 7 days on the primary calendar).",
        ListEventsInput,
        _list_events,
    ),
    (
        "gcal_search_events",
        "Search Google Calendar events by free text within a time window.",
        SearchEventsInput,
        _search_events,
    ),
    ("gcal_get_event", "Get full details of one Google Calendar event by id.", GetEventInput, _get_event),
    (
        "gcal_create_event",
        "Create a Google Calendar event. Use RFC3339 datetimes (or YYYY-MM-DD for all-day).",
        CreateEventInput,
        _create_event,
    ),
]


def register_gcal_tools(registry: ToolRegistry, client: GoogleClient) -> None:
    for name, desc, model, fn in _TOOLS:
        registry.register(name, desc, model, (lambda f: lambda inp, ctx: f(inp, ctx, client))(fn))
