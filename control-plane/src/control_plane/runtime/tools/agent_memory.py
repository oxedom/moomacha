"""Agent memory exposed to agents as tools, backed by the Redis Cloud managed
Agent Memory service via its REST API (httpx.AsyncClient).

Design:
- Long-term memory is namespaced into five tiers (agent / topic / channel /
  workspace). Writes resolve a `scope` to a namespace; reads (search) are
  filtered to the agent's read-set (its own agent tier + the channel tier).
  channel/workspace writes are gated to librarian agents.
- Working (session) memory is keyed per Zulip topic via ``session_id_for`` and
  is independent of the long-term namespace tiers.

REST endpoints (Redis Cloud base: {endpoint}/v1/stores/{store_id}/):
  POST long-term-memory           – create memories (with namespace/memoryType)
  POST long-term-memory/search    – semantic search (namespace-filtered)
  POST session-memory/events      – append session event
  GET  session-memory/{session}   – read session

For a local Redis Agent Memory Server, omit store_id/api_key. The client then
uses the open-source server's /v1 long-term-memory and working-memory endpoints.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, Field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult

OUTPUT_CAP = 12_000
# The Agent Memory service requires session/actor IDs to contain only
# alphanumerics and hyphens — slash, dot, and underscore are all rejected (400).
_SESSION_RE = re.compile(r"[^A-Za-z0-9-]+")


def session_id_for(channel: str, topic: str) -> str:
    """Stable per-topic key for working (session) memory."""
    raw = f"{channel}-{topic}"
    return _SESSION_RE.sub("-", raw).strip("-") or "unknown"


WORKSPACE_NS = "workspace"


def wire_ns(ns: str) -> str:
    """Encode a logical namespace (e.g. ``agent:archetype:researcher`` or
    ``topic:sandbox/Project X``) to the Agent Memory store's charset: it accepts
    only alphanumerics and hyphens, rejecting ``:``/``/``/space with a 400. We
    keep the readable colon form in Postgres/ToolContext and slugify only here,
    at the Redis boundary. Both writes and search apply this identically, so the
    encoded namespaces still match."""
    return _SESSION_RE.sub("-", ns).strip("-") or "ns"


def channel_ns(channel: str) -> str:
    return f"channel:{channel}"


def topic_ns(channel: str, topic: str) -> str:
    return f"topic:{channel}/{topic}"


def resolve_scope(scope: str, ctx: ToolContext) -> tuple[str | None, str | None]:
    """Map a write `scope` to (namespace, tier). tier in {self,topic,channel,workspace}.

    `self` uses the agent-tier namespace carried on the ToolContext (archetype for
    cattle, agent id for persistent agents); falls back to ``agent:{agent.id}``.
    """
    if scope == "self":
        ns = ctx.memory_ns or f"agent:{getattr(ctx.agent, 'id', 'unknown')}"
        return ns, "self"
    if scope == "topic":
        return topic_ns(ctx.channel, ctx.topic), "topic"
    if scope == "channel":
        return channel_ns(ctx.channel), "channel"
    if scope == "workspace":
        return WORKSPACE_NS, "workspace"
    return None, None


_LIBRARIAN_TIERS = {"channel", "workspace"}


def _gate_write(scope: str, ctx: ToolContext) -> tuple[str | None, str | None]:
    """Resolve scope to (namespace, error). error is non-None when the write is refused."""
    ns, tier = resolve_scope(scope, ctx)
    if ns is None:
        return None, f"Unknown scope '{scope}'. Use 'self' or 'topic'."
    if tier in _LIBRARIAN_TIERS and not getattr(ctx.agent, "is_librarian", False):
        return None, (
            f"Writing to {tier} memory requires the librarian — @-mention the librarian "
            f"in this topic and ask it to remember this."
        )
    return ns, None


def _cap(value: str, limit: int = OUTPUT_CAP) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated to {limit} characters ..."


class AgentMemoryRest:
    """Thin async REST client for Redis Cloud or local Agent Memory Server."""

    def __init__(
        self,
        *,
        endpoint: str,
        store_id: str | None = None,
        api_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._cloud_mode = bool(store_id)
        self._base = (
            f"{endpoint.rstrip('/')}/v1/stores/{store_id}"
            if store_id
            else f"{endpoint.rstrip('/')}/v1"
        )
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._timeout = timeout

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(f"{self._base}/{path}", json=body, headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(f"{self._base}/{path}", headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def create_memories(self, memories: list[dict[str, Any]]) -> str:
        # Cloud expects memoryType; local AMS prefers memory_type.
        if self._cloud_mode:
            payload = {"memories": memories}
            path = "long-term-memory"
        else:
            payload = {"memories": [_local_memory_record(m) for m in memories]}
            path = "long-term-memory/"
        result = await self._post(path, payload)
        return str(result)

    async def search_memories(
        self, text: str, limit: int = 5, namespaces: list[str] | None = None
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"text": text, "limit": limit}
        if namespaces:
            if self._cloud_mode:
                body["namespace"] = namespaces  # forward-compat; older managed stores ignored this
            else:
                body["filters"] = {"namespace": {"in": namespaces}}
        result = await self._post("long-term-memory/search", body)
        if not isinstance(result, dict):
            return []
        return result.get("items") or result.get("memories") or []

    async def add_session_event(self, session_id: str, text: str, role: str = "USER") -> str:
        from datetime import datetime, timezone
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self._cloud_mode:
            existing = await self._get_local_working_memory(session_id)
            messages = existing.get("messages", []) if isinstance(existing, dict) else []
            messages.append({"role": role.lower(), "content": text, "created_at": created_at})
            result = await self._put(
                f"working-memory/{session_id}",
                {"session_id": session_id, "messages": messages},
            )
            return str(result)
        result = await self._post(
            "session-memory/events",
            {
                "sessionId": session_id,
                "actorId": session_id,
                "role": role,
                "content": [{"text": text}],
                # The service requires an ISO 8601 / RFC 3339 timestamp string,
                # not epoch milliseconds (a plain int yields HTTP 400).
                "createdAt": created_at,
            },
        )
        return str(result)

    async def get_session(self, session_id: str) -> str:
        path = f"session-memory/{session_id}" if self._cloud_mode else f"working-memory/{session_id}"
        result = await self._get(path)
        return str(result)

    async def _put(self, path: str, body: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.put(f"{self._base}/{path}", json=body, headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def _get_local_working_memory(self, session_id: str) -> dict[str, Any]:
        try:
            result = await self._get(f"working-memory/{session_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {}
            raise
        return result if isinstance(result, dict) else {}


def _local_memory_record(memory: dict[str, Any]) -> dict[str, Any]:
    result = dict(memory)
    if "memoryType" in result and "memory_type" not in result:
        result["memory_type"] = result.pop("memoryType")
    return result


# --- Agent-facing input models ------------------------------------------------


class SearchMemoryInput(BaseModel):
    query: str = Field(description="What to search for in long-term memory.")
    limit: int = Field(default=5, ge=1, le=20, description="Max memories to return.")


class RememberInput(BaseModel):
    text: str = Field(description="A durable fact to store in long-term memory.")
    scope: str = Field(
        default="self",
        description="Where to store it: 'self' (your own memory) or 'topic'. "
        "'channel'/'workspace' require the librarian.",
    )


class RecordEpisodeInput(BaseModel):
    text: str = Field(description="An event that happened, to store as episodic memory.")
    event_date: str | None = Field(
        default=None, description="ISO 8601 date of the event (e.g. 2026-05-25), if known."
    )
    scope: str = Field(
        default="self",
        description="'self' or 'topic'. 'channel'/'workspace' require the librarian.",
    )


class SetWorkingMemoryInput(BaseModel):
    data: str = Field(description="Text to store in this topic's short-term session memory.")


class GetWorkingMemoryInput(BaseModel):
    pass  # no inputs needed — scope from ToolContext


# --- Adapters -----------------------------------------------------------------


async def _search(inp: SearchMemoryInput, ctx: ToolContext, rest: AgentMemoryRest) -> ToolResult:
    self_ns = ctx.memory_ns or f"agent:{getattr(ctx.agent, 'id', 'unknown')}"
    read_set = [wire_ns(self_ns), wire_ns(channel_ns(ctx.channel))]
    allowed = set(read_set)
    # The managed store does not filter search by namespace, so over-fetch and
    # filter to the agent's read-set here (its own tier + the channel tier).
    raw = await rest.search_memories(inp.query, max(inp.limit * 5, 25), namespaces=read_set)
    scoped = [it for it in raw if it.get("namespace") in allowed][: inp.limit]
    if not scoped:
        return ToolResult(ok=True, content="(no relevant memories in your scope)")
    lines = [f"- {it.get('text', '')}" for it in scoped]
    return ToolResult(ok=True, content=_cap("\n".join(lines)))


async def _remember(inp: RememberInput, ctx: ToolContext, rest: AgentMemoryRest) -> ToolResult:
    ns, err = _gate_write(inp.scope, ctx)
    if err:
        return ToolResult(ok=False, content=err)
    memory_id = str(uuid.uuid4())
    out = await rest.create_memories(
        [{"id": memory_id, "text": inp.text, "namespace": wire_ns(ns), "memoryType": "semantic"}]
    )
    return ToolResult(ok=True, content=out or "Stored.")


async def _record_episode(inp: RecordEpisodeInput, ctx: ToolContext, rest: AgentMemoryRest) -> ToolResult:
    ns, err = _gate_write(inp.scope, ctx)
    if err:
        return ToolResult(ok=False, content=err)
    memory: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "text": inp.text,
        "namespace": wire_ns(ns),
        "memoryType": "episodic",
    }
    if inp.event_date:
        memory["event_date"] = inp.event_date
    out = await rest.create_memories([memory])
    return ToolResult(ok=True, content=out or "Recorded.")


async def _set_working(inp: SetWorkingMemoryInput, ctx: ToolContext, rest: AgentMemoryRest) -> ToolResult:
    sid = session_id_for(ctx.channel, ctx.topic)
    out = await rest.add_session_event(sid, inp.data, role="ASSISTANT")
    return ToolResult(ok=True, content=out or "Saved to working memory.")


async def _get_working(inp: GetWorkingMemoryInput, ctx: ToolContext, rest: AgentMemoryRest) -> ToolResult:
    sid = session_id_for(ctx.channel, ctx.topic)
    out = await rest.get_session(sid)
    return ToolResult(ok=True, content=_cap(out))


def register_agent_memory_tools(registry: ToolRegistry, rest: AgentMemoryRest) -> None:
    """Register the four memory tools. Called only when memory is enabled."""
    registry.register(
        "search_long_term_memory",
        "Semantic search over your long-term memory (your agent memory + this channel).",
        SearchMemoryInput,
        lambda inp, ctx: _search(inp, ctx, rest),
    )
    registry.register(
        "remember",
        "Save a durable fact to your long-term memory. scope: 'self' (default) or 'topic'; channel/org require the librarian.",
        RememberInput,
        lambda inp, ctx: _remember(inp, ctx, rest),
    )
    registry.register(
        "record_episode",
        "Record an event as episodic long-term memory (optionally dated).",
        RecordEpisodeInput,
        lambda inp, ctx: _record_episode(inp, ctx, rest),
    )
    registry.register(
        "set_working_memory",
        "Append text to this topic's short-term session memory.",
        SetWorkingMemoryInput,
        lambda inp, ctx: _set_working(inp, ctx, rest),
    )
    registry.register(
        "get_working_memory",
        "Read back this topic's short-term session memory.",
        GetWorkingMemoryInput,
        lambda inp, ctx: _get_working(inp, ctx, rest),
    )
