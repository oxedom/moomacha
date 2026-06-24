from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import AgentRow, EventRow

ERROR_EVENT_TYPES = frozenset({"error", "schedule_errored"})


def build_dashboard_router(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    clock: Callable[[], datetime] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["dashboard"])
    now = clock or (lambda: datetime.now(UTC))

    @router.get("/dashboard/snapshot")
    async def dashboard_snapshot() -> dict[str, Any]:
        return await build_dashboard_snapshot(session_factory, now=now())

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> str:
        return DASHBOARD_HTML

    return router


async def build_dashboard_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> dict[str, Any]:
    now = _as_utc(now)
    cutoff = now - timedelta(hours=1)

    async with session_factory() as session:
        agents = list(
            (
                await session.execute(
                    select(AgentRow).order_by(AgentRow.name, AgentRow.created_at)
                )
            )
            .scalars()
            .all()
        )
        recent_hour_events = list(
            (
                await session.execute(
                    select(EventRow).where(EventRow.timestamp >= cutoff)
                )
            )
            .scalars()
            .all()
        )
        recent_events = list(
            (
                await session.execute(
                    select(EventRow)
                    .order_by(EventRow.timestamp.desc(), EventRow.id.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        last_events = await _load_last_events_by_agent(session, agents)

    agent_by_id = {agent.id: agent for agent in agents}
    return {
        "generated_at": _isoformat(now),
        "summary": {
            "agents_total": len(agents),
            "agents_enabled": sum(1 for agent in agents if agent.enabled),
            "replies_last_hour": sum(
                1
                for event in recent_hour_events
                if event.event_type == "turn.end" and event.status == "ok"
            ),
            "errors_last_hour": sum(
                1 for event in recent_hour_events if event.event_type in ERROR_EVENT_TYPES
            ),
            "active_channels_last_hour": len(
                {
                    event.related_channel
                    for event in recent_hour_events
                    if event.related_channel
                }
            ),
        },
        "agents": [
            _serialize_agent(agent, last_events.get(agent.id))
            for agent in agents
        ],
        "events": [
            _serialize_event(event, agent_by_id)
            for event in recent_events
        ],
    }


async def _load_last_events_by_agent(
    session: AsyncSession,
    agents: list[AgentRow],
) -> dict[Any, EventRow]:
    agent_ids = [agent.id for agent in agents]
    if not agent_ids:
        return {}

    rows = list(
        (
            await session.execute(
                select(EventRow)
                .where(
                    or_(
                        EventRow.related_agent_id.in_(agent_ids),
                        EventRow.actor_id.in_(agent_ids),
                    )
                )
                .order_by(EventRow.timestamp.desc(), EventRow.id.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[Any, EventRow] = {}
    agent_ids_set = set(agent_ids)
    for row in rows:
        agent_id = row.related_agent_id or row.actor_id
        if agent_id in agent_ids_set and agent_id not in latest:
            latest[agent_id] = row
    return latest


def _serialize_agent(agent: AgentRow, last_event: EventRow | None) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "name": agent.name,
        "model_id": agent.model_id,
        "enabled": agent.enabled,
        "is_bastion": agent.is_bastion,
        "provisioning_status": agent.provisioning_status,
        "readable_channels": list(agent.readable_channels or []),
        "last_seen": _isoformat(last_event.timestamp) if last_event else None,
        "last_event_type": last_event.event_type if last_event else None,
    }


def _serialize_event(
    event: EventRow,
    agent_by_id: dict[Any, AgentRow],
) -> dict[str, Any]:
    agent_id = event.related_agent_id or event.actor_id
    agent = agent_by_id.get(agent_id)
    return {
        "timestamp": _isoformat(event.timestamp),
        "event_type": event.event_type,
        "actor_type": event.actor_type,
        "agent_name": agent.name if agent else None,
        "channel": event.related_channel,
        "source_message_id": event.source_message_id,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _isoformat(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Live Agent Monitor</title>
  <link rel="icon" href="data:,">
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-stone-50 text-zinc-950 antialiased">
  <main class="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-5 sm:px-6 lg:px-8">
    <header class="flex flex-col gap-3 border-b border-zinc-200 pb-5 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <p class="text-xs font-semibold uppercase tracking-widest text-teal-700">Control plane</p>
        <h1 class="mt-1 text-3xl font-semibold tracking-normal text-zinc-950">Live Agent Monitor</h1>
      </div>
      <div class="flex flex-wrap items-center gap-3 text-sm text-zinc-600">
        <span id="generated-at">Waiting for snapshot</span>
        <span id="connection-status" class="rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1 font-medium text-amber-900">Connecting</span>
      </div>
    </header>

    <section id="summary" class="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" aria-label="Summary"></section>

    <section class="grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
      <div class="min-w-0">
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-lg font-semibold tracking-normal">Team Roster</h2>
          <span id="agent-count" class="text-sm text-zinc-500"></span>
        </div>
        <div class="overflow-x-auto rounded-lg border border-zinc-200 bg-white">
          <table class="w-full min-w-[860px] border-collapse text-sm">
            <thead class="bg-zinc-100 text-left text-xs uppercase tracking-widest text-zinc-500">
              <tr>
                <th class="px-4 py-3 font-semibold">Agent</th>
                <th class="px-4 py-3 font-semibold">State</th>
                <th class="px-4 py-3 font-semibold">Model</th>
                <th class="px-4 py-3 font-semibold">Channels</th>
                <th class="px-4 py-3 font-semibold">Last Seen</th>
                <th class="px-4 py-3 font-semibold">Last Event</th>
              </tr>
            </thead>
            <tbody id="agents-body" class="divide-y divide-zinc-200"></tbody>
          </table>
          <div id="agents-empty" class="hidden px-4 py-8 text-sm text-zinc-500">No agents yet.</div>
        </div>
      </div>

      <div class="min-w-0">
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-lg font-semibold tracking-normal">Recent Activity</h2>
          <span id="event-count" class="text-sm text-zinc-500"></span>
        </div>
        <div id="events-list" class="flex max-h-[680px] flex-col overflow-y-auto rounded-lg border border-zinc-200 bg-white"></div>
        <div id="events-empty" class="hidden rounded-lg border border-zinc-200 bg-white px-4 py-8 text-sm text-zinc-500">No recent activity.</div>
      </div>
    </section>
  </main>

  <script>
    const errorTypes = new Set(["error", "schedule_errored"]);
    const summaryEl = document.getElementById("summary");
    const statusEl = document.getElementById("connection-status");
    const generatedAtEl = document.getElementById("generated-at");
    const agentsBody = document.getElementById("agents-body");
    const agentsEmpty = document.getElementById("agents-empty");
    const eventsList = document.getElementById("events-list");
    const eventsEmpty = document.getElementById("events-empty");
    const agentCount = document.getElementById("agent-count");
    const eventCount = document.getElementById("event-count");

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      })[char]);
    }

    function relativeTime(value) {
      if (!value) return "No activity";
      const date = new Date(value);
      const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
      if (seconds < 45) return "just now";
      if (seconds < 90) return "1m ago";
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return `${minutes}m ago`;
      const hours = Math.floor(minutes / 60);
      if (hours < 48) return `${hours}h ago`;
      const days = Math.floor(hours / 24);
      return `${days}d ago`;
    }

    function pill(text, tone) {
      const tones = {
        green: "border-emerald-200 bg-emerald-50 text-emerald-800",
        red: "border-red-200 bg-red-50 text-red-800",
        amber: "border-amber-200 bg-amber-50 text-amber-900",
        zinc: "border-zinc-200 bg-zinc-100 text-zinc-700",
        teal: "border-teal-200 bg-teal-50 text-teal-800"
      };
      return `<span class="inline-flex rounded-md border px-2 py-0.5 text-xs font-medium ${tones[tone] || tones.zinc}">${escapeHtml(text)}</span>`;
    }

    function renderSummary(summary) {
      const items = [
        ["Agents", summary.agents_total, `${summary.agents_enabled} enabled`, "teal"],
        ["Enabled", summary.agents_enabled, "ready for mentions", "green"],
        ["Replies", summary.replies_last_hour, "last hour", "zinc"],
        ["Errors", summary.errors_last_hour, "last hour", summary.errors_last_hour > 0 ? "red" : "green"],
        ["Channels", summary.active_channels_last_hour, "active last hour", "amber"]
      ];
      summaryEl.innerHTML = items.map(([label, value, note, tone]) => {
        const color = tone === "red" ? "border-red-200 bg-red-50" : "border-zinc-200 bg-white";
        const number = tone === "red" ? "text-red-700" : tone === "teal" ? "text-teal-800" : "text-zinc-950";
        return `
          <article class="rounded-lg border ${color} px-4 py-3">
            <div class="text-xs font-semibold uppercase tracking-widest text-zinc-500">${escapeHtml(label)}</div>
            <div class="mt-2 text-3xl font-semibold tracking-normal ${number}">${escapeHtml(value)}</div>
            <div class="mt-1 text-sm text-zinc-500">${escapeHtml(note)}</div>
          </article>
        `;
      }).join("");
    }

    function renderAgents(agents) {
      agentCount.textContent = `${agents.length} total`;
      agentsEmpty.classList.toggle("hidden", agents.length !== 0);
      agentsBody.innerHTML = agents.map((agent) => {
        const stale = !agent.last_seen || (Date.now() - new Date(agent.last_seen).getTime()) > 60 * 60 * 1000;
        const rowTone = !agent.enabled ? "bg-zinc-50 text-zinc-500" : stale ? "bg-amber-50/40" : "bg-white";
        const state = [
          agent.enabled ? pill("Enabled", "green") : pill("Disabled", "zinc"),
          agent.is_bastion ? pill("Bastion", "teal") : "",
          pill(agent.provisioning_status || "unknown", agent.provisioning_status === "active" ? "green" : "amber")
        ].filter(Boolean).join(" ");
        const channels = (agent.readable_channels || []).length
          ? agent.readable_channels.map((channel) => pill(channel, "zinc")).join(" ")
          : `<span class="text-zinc-400">None</span>`;
        return `
          <tr class="${rowTone}">
            <td class="px-4 py-3">
              <div class="font-medium text-zinc-950">${escapeHtml(agent.name)}</div>
              <div class="mt-1 text-xs text-zinc-500">${escapeHtml(agent.id)}</div>
            </td>
            <td class="px-4 py-3"><div class="flex flex-wrap gap-1.5">${state}</div></td>
            <td class="px-4 py-3 text-zinc-700">${escapeHtml(agent.model_id)}</td>
            <td class="px-4 py-3"><div class="flex flex-wrap gap-1.5">${channels}</div></td>
            <td class="px-4 py-3 ${stale ? "font-medium text-amber-800" : "text-zinc-700"}">${escapeHtml(relativeTime(agent.last_seen))}</td>
            <td class="px-4 py-3 text-zinc-700">${escapeHtml(agent.last_event_type || "-")}</td>
          </tr>
        `;
      }).join("");
    }

    function renderEvents(events) {
      eventCount.textContent = `${events.length} shown`;
      eventsEmpty.classList.toggle("hidden", events.length !== 0);
      eventsList.classList.toggle("hidden", events.length === 0);
      eventsList.innerHTML = events.map((event) => {
        const isError = errorTypes.has(event.event_type);
        const border = isError ? "border-l-red-500 bg-red-50/70" : "border-l-teal-500 bg-white";
        return `
          <article class="border-b border-zinc-200 border-l-4 ${border} px-4 py-3 last:border-b-0">
            <div class="flex items-start justify-between gap-4">
              <div class="min-w-0">
                <div class="font-medium text-zinc-950">${escapeHtml(event.event_type)}</div>
                <div class="mt-1 text-sm text-zinc-600">
                  ${escapeHtml(event.agent_name || event.actor_type || "system")}
                  ${event.channel ? `in ${escapeHtml(event.channel)}` : ""}
                </div>
              </div>
              <time class="shrink-0 text-xs font-medium text-zinc-500">${escapeHtml(relativeTime(event.timestamp))}</time>
            </div>
            ${event.source_message_id ? `<div class="mt-2 text-xs text-zinc-500">Message ${escapeHtml(event.source_message_id)}</div>` : ""}
          </article>
        `;
      }).join("");
    }

    function setStatus(text, reconnecting) {
      statusEl.textContent = text;
      statusEl.className = reconnecting
        ? "rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1 font-medium text-amber-900"
        : "rounded-md border border-emerald-300 bg-emerald-50 px-2.5 py-1 font-medium text-emerald-800";
    }

    function render(snapshot) {
      generatedAtEl.textContent = `Snapshot ${relativeTime(snapshot.generated_at)}`;
      renderSummary(snapshot.summary);
      renderAgents(snapshot.agents || []);
      renderEvents(snapshot.events || []);
    }

    async function loadSnapshot() {
      try {
        const response = await fetch("/dashboard/snapshot", { cache: "no-store" });
        if (!response.ok) throw new Error(`snapshot ${response.status}`);
        render(await response.json());
        setStatus("Live", false);
      } catch (error) {
        setStatus("Reconnecting...", true);
      }
    }

    loadSnapshot();
    setInterval(loadSnapshot, 3000);
  </script>
</body>
</html>
"""
