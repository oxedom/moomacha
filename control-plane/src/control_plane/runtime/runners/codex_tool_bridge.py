# src/control_plane/runtime/runners/codex_tool_bridge.py
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import secrets
from dataclasses import dataclass
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount

from control_plane.runtime.tools.registry import ToolRegistry

logger = logging.getLogger("control_plane")

TOOL_PREFIX = ""  # no prefix: codex built-ins (bash/edit/etc) don't overlap with CP tool names

_CURRENT_TOKEN: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "codex_bridge_token", default=None
)


class _BearerCapture:
    """Pure-ASGI middleware: stash the request's bearer token into a ContextVar so
    in-handler MCP code can resolve the per-turn session."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
        reset = _CURRENT_TOKEN.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _CURRENT_TOKEN.reset(reset)


@dataclass
class BridgeSession:
    ctx: Any        # the live per-turn ToolContext from RunnerInput
    runtime: Any    # ToolRuntime — dispatch chokepoint (validation + permission + events)
    allowed: list[str]


class CodexToolBridge:
    """In-process loopback MCP bridge. A codex turn mints a token bound to its live
    ToolContext + ToolRuntime + allowed_tools; codex calls cp__<tool> over MCP and
    the bridge dispatches through ToolRuntime.execute (reusing permission + events)."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._sessions: dict[str, BridgeSession] = {}

    def mint(self, *, ctx: Any, runtime: Any, allowed: list[str]) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = BridgeSession(ctx=ctx, runtime=runtime, allowed=list(allowed))
        return token

    def release(self, token: str) -> None:
        self._sessions.pop(token, None)

    def _session(self, token: str | None) -> BridgeSession:
        session = self._sessions.get(token) if token else None
        if session is None:
            raise PermissionError("unknown or expired codex bridge token")
        return session

    def tools_for(self, token: str | None) -> list[dict]:
        """MCP tool descriptors for the token's allowed tools (cp__-prefixed)."""
        session = self._session(token)
        out: list[dict] = []
        for name in session.allowed:
            entry = self._registry.get(name)
            if entry is None:
                logger.debug("codex bridge: allowed tool %r not registered; skipping", name)
                continue
            out.append({
                "name": f"{TOOL_PREFIX}{name}",
                "description": entry.description,
                "inputSchema": entry.input_model.model_json_schema(),
            })
        return out

    async def _dispatch(self, token: str | None, mcp_name: str, arguments: dict) -> str:
        session = self._session(token)
        name = mcp_name[len(TOOL_PREFIX):] if mcp_name.startswith(TOOL_PREFIX) else mcp_name
        if name not in session.allowed:
            raise PermissionError(f"tool {name!r} not permitted for this codex turn")
        result = await session.runtime.execute(name, json.dumps(arguments or {}), session.ctx)
        if not result.ok:
            raise RuntimeError(result.content)
        return result.content

    def app(self):
        """Return the loopback ASGI app: a Starlette mount serving a stateless
        streamable-HTTP MCP server, wrapped in bearer-token capture."""
        server: Server = Server("codex-tool-bridge")

        @server.list_tools()
        async def _list_tools() -> list[types.Tool]:
            descriptors = self.tools_for(_CURRENT_TOKEN.get())
            return [
                types.Tool(
                    name=d["name"],
                    description=d["description"],
                    inputSchema=d["inputSchema"],
                )
                for d in descriptors
            ]

        @server.call_tool()
        async def _call_tool(name: str, arguments: dict) -> list[types.ContentBlock]:
            text = await self._dispatch(_CURRENT_TOKEN.get(), name, arguments or {})
            return [types.TextContent(type="text", text=text)]

        manager = StreamableHTTPSessionManager(
            app=server, json_response=True, stateless=True
        )

        async def _handle(scope, receive, send):
            await manager.handle_request(scope, receive, send)

        @contextlib.asynccontextmanager
        async def _lifespan(_app):
            async with manager.run():
                yield

        star = Starlette(routes=[Mount("/mcp", app=_handle)], lifespan=_lifespan)
        return _BearerCapture(star)
