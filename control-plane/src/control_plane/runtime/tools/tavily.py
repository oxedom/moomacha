"""Web access exposed to agents as tools, backed by Tavily's hosted remote MCP
server over **streamable HTTP**.

Design (docs/superpowers/specs/2026-05-25-tavily-mcp-design.md):

- The control plane is an MCP *client*. All MCP wiring lives behind the injectable
  ``TavilyMcp`` wrapper (mirrors ``AgentMemoryMcp`` in ``agent_memory.py``), so the
  adapters and their unit tests never touch the network.
- Unlike agent-memory there is no per-topic scope to enforce: adapters forward the
  model's validated args straight through to Tavily.
- Transport differs from agent-memory: Tavily's hosted MCP is streamable HTTP
  (``mcp.client.streamable_http.streamablehttp_client``), not SSE.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

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


class TavilyMcp:
    """Thin async MCP (streamable HTTP) client for Tavily's hosted server.

    ``call_tool`` opens a fresh session per call: robust against stale connections
    and matches the per-call-client style used by ``AgentMemoryMcp``. The MCP
    imports are local so the package is only loaded when Tavily is enabled.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._url = url
        # Bearer header keeps the key out of URLs/logs; Tavily accepts both this
        # and ?tavilyApiKey= in the URL.
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout_seconds

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(
            self._url, headers=self._headers, timeout=self._timeout
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
        text = _flatten_content(result)
        if getattr(result, "isError", False):
            raise RuntimeError(text or f"MCP tool '{name}' returned an error")
        return _cap(text or "(no content)")


# --- Agent-facing input models (mirror Tavily's published arg names) ----------


# Each model exposes a useful subset of Tavily's published parameters. Further
# fields (e.g. search: topic/country/start_date/end_date/include_images;
# extract: include_images; crawl: extract_depth/format) are intentionally
# deferred — add them in a follow-up if agents need them.
class TavilySearchInput(BaseModel):
    query: str = Field(description="The web search query.")
    search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = Field(
        default="basic", description="'advanced' is more thorough; 'fast' optimizes latency."
    )
    max_results: int = Field(default=5, ge=1, le=20, description="Max results to return.")
    time_range: Literal["day", "week", "month", "year"] | None = Field(
        default=None, description="Restrict results to this window back from today."
    )
    include_domains: list[str] = Field(default_factory=list, description="Only these domains.")
    exclude_domains: list[str] = Field(default_factory=list, description="Never these domains.")
    include_raw_content: bool = Field(
        default=False, description="Include cleaned full-page content, not just snippets."
    )


class TavilyExtractInput(BaseModel):
    urls: list[str] = Field(description="URLs to extract clean content from.")
    extract_depth: Literal["basic", "advanced"] = Field(
        default="basic", description="'advanced' for tables/embedded content or protected sites."
    )
    format: Literal["markdown", "text"] = Field(default="markdown", description="Output format.")


class TavilyCrawlInput(BaseModel):
    url: str = Field(description="The root URL to begin crawling.")
    max_depth: int = Field(default=1, ge=1, description="How far from the base URL to explore.")
    max_breadth: int = Field(default=20, ge=1, description="Max links to follow per page.")
    limit: int = Field(default=50, ge=1, description="Total links to process before stopping.")
    instructions: str = Field(default="", description="Natural-language guidance for the crawler.")


class TavilyMapInput(BaseModel):
    url: str = Field(description="The root URL to map.")
    max_depth: int = Field(default=1, ge=1, description="How far from the base URL to explore.")
    max_breadth: int = Field(default=20, ge=1, description="Max links to follow per page.")
    limit: int = Field(default=50, ge=1, description="Total links to process before stopping.")
    instructions: str = Field(default="", description="Natural-language guidance for the mapper.")


class TavilyResearchInput(BaseModel):
    input: str = Field(description="A comprehensive description of the research task.")
    model: Literal["mini", "pro", "auto"] = Field(
        default="auto", description="'mini' for narrow tasks, 'pro' for broad multi-subtopic ones."
    )


# --- Adapters: validate input, forward args straight through to Tavily --------


def _make_adapter(tool_name: str, mcp: TavilyMcp):
    async def _adapter(inp: BaseModel, ctx: ToolContext) -> ToolResult:
        # exclude_unset: forward only the fields the LLM actually set, so Tavily
        # applies its own defaults rather than us echoing ours into the payload.
        out = await mcp.call_tool(tool_name, inp.model_dump(exclude_unset=True))
        return ToolResult(ok=True, content=out)

    return _adapter


_TOOLS: list[tuple[str, str, type[BaseModel]]] = [
    (
        "tavily_search",
        "Search the web for current information; returns snippets and source URLs.",
        TavilySearchInput,
    ),
    (
        "tavily_extract",
        "Extract clean page content from one or more specific URLs.",
        TavilyExtractInput,
    ),
    (
        "tavily_crawl",
        "Crawl a website from a root URL, extracting page content with configurable depth/breadth.",
        TavilyCrawlInput,
    ),
    (
        "tavily_map",
        "Map a website's structure; returns the list of URLs found from the base URL.",
        TavilyMapInput,
    ),
    (
        "tavily_research",
        "Run comprehensive multi-source research on a topic and return a detailed answer.",
        TavilyResearchInput,
    ),
]


def register_tavily_tools(registry: ToolRegistry, mcp: TavilyMcp) -> None:
    """Register all five Tavily tools. Called only when Tavily is enabled, so the
    tools are simply absent from the schema when the flag is off."""
    for name, description, model in _TOOLS:
        registry.register(name, description, model, _make_adapter(name, mcp))
