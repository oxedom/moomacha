"""Context7 docs-lookup tools exposed to agents.

Context7 ships as a stdio MCP server (`npx -y @upstash/context7-mcp`). A fresh
per-call session is opened — same pattern as ExecMcp (SSE) and TavilyMcp (HTTP).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult

logger = logging.getLogger("control_plane")

DEFAULT_TIMEOUT_SECONDS = 30.0
OUTPUT_CAP = 12_000


def _cap(value: str, limit: int = OUTPUT_CAP) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated to {limit} characters ..."


def _flatten_content(result: Any) -> str:
    """Join the text blocks of a CallToolResult into one string."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


class Context7Mcp:
    """Thin async MCP (stdio) client for Context7.

    Opens a fresh npx subprocess per call; no persistent daemon.
    """

    def __init__(
        self,
        *,
        command: list[str],
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._command = command
        self._timeout = timeout_seconds

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(command=self._command[0], args=self._command[1:])

        async def _invoke() -> str:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
            text = _flatten_content(result)
            if getattr(result, "isError", False):
                raise RuntimeError(text or f"MCP tool '{name}' returned an error")
            return _cap(text or "(no content)")

        return await asyncio.wait_for(_invoke(), timeout=self._timeout)


class Context7ResolveInput(BaseModel):
    library_name: str = Field(description="Library or framework name to resolve, e.g. 'next.js'.")
    query: str = Field(default="", description="User question — improves relevance ranking.")


class Context7QueryInput(BaseModel):
    library_id: str = Field(description="Context7 library ID, e.g. '/vercel/next.js'.")
    query: str = Field(description="Specific question or topic to fetch documentation for.")
    tokens: int = Field(default=5000, ge=1000, le=20000, description="Max doc tokens returned.")


def _make_adapter(mcp_tool_name: str, mcp: Context7Mcp):
    async def _adapter(inp: BaseModel, ctx: ToolContext) -> ToolResult:
        out = await mcp.call_tool(mcp_tool_name, inp.model_dump(exclude_unset=True))
        return ToolResult(ok=True, content=out)
    return _adapter


_TOOLS: list[tuple[str, str, str, type[BaseModel]]] = [
    (
        "context7_resolve_library",
        "resolve-library-id",
        "Resolve a library name to a Context7 library ID. Call this first before fetching docs.",
        Context7ResolveInput,
    ),
    (
        "context7_query_docs",
        "get-library-docs",
        "Fetch current documentation for a Context7 library ID. Use the ID from context7_resolve_library.",
        Context7QueryInput,
    ),
]


def register_context7_tools(registry: ToolRegistry, mcp: Context7Mcp) -> None:
    for agent_name, mcp_name, description, model in _TOOLS:
        registry.register(agent_name, description, model, _make_adapter(mcp_name, mcp))
