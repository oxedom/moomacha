"""Network-free tests for the hosted-Tavily MCP tools.

A fake TavilyMcp records the (name, arguments) of each call, so we assert that
adapters forward the model's args unchanged and that failures degrade to a
not-ok ToolResult. No MCP/HTTP connection is opened.
"""

from dataclasses import dataclass, field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime
from control_plane.runtime.tools.tavily import TavilyMcp, register_tavily_tools

ALL_TOOLS = ["tavily_search", "tavily_extract", "tavily_crawl", "tavily_map", "tavily_research"]


class FakeMcp:
    def __init__(self, reply: str = "ok", raises: bool = False) -> None:
        self.reply = reply
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.raises:
            raise RuntimeError("tavily down")
        return self.reply


@dataclass
class FakeAgent:
    id: str = "agent-7"
    allowed_tools: list[str] = field(default_factory=lambda: list(ALL_TOOLS))
    is_bastion: bool = False


def _ctx() -> ToolContext:
    return ToolContext(agent=FakeAgent(), zulip=None, channel="sandbox", topic="Project X")


def _registry(mcp) -> ToolRegistry:
    reg = ToolRegistry()
    register_tavily_tools(reg, mcp)
    return reg


async def test_search_forwards_only_set_fields():
    mcp = FakeMcp(reply="result text")
    runtime = ToolRuntime(_registry(mcp))

    result = await runtime.execute(
        "tavily_search", '{"query": "quantum computing 2026", "max_results": 3}', _ctx()
    )

    assert result.ok and result.content == "result text"
    name, args = mcp.calls[0]
    assert name == "tavily_search"
    # exclude_unset: only the fields the LLM actually set are forwarded; defaults
    # (search_depth, include_domains, time_range, ...) are left for Tavily to apply.
    assert args == {"query": "quantum computing 2026", "max_results": 3}


async def test_search_forwards_advanced_depth_and_domains():
    mcp = FakeMcp()
    runtime = ToolRuntime(_registry(mcp))

    await runtime.execute(
        "tavily_search",
        '{"query": "x", "search_depth": "advanced", "include_domains": ["arxiv.org"]}',
        _ctx(),
    )

    _, args = mcp.calls[0]
    assert args["search_depth"] == "advanced"
    assert args["include_domains"] == ["arxiv.org"]


async def test_extract_forwards_urls():
    mcp = FakeMcp()
    runtime = ToolRuntime(_registry(mcp))

    await runtime.execute("tavily_extract", '{"urls": ["https://a.com", "https://b.com"]}', _ctx())

    name, args = mcp.calls[0]
    assert name == "tavily_extract"
    assert args["urls"] == ["https://a.com", "https://b.com"]


async def test_research_uses_input_field_not_query():
    mcp = FakeMcp(reply="report")
    runtime = ToolRuntime(_registry(mcp))

    result = await runtime.execute(
        "tavily_research", '{"input": "compare EV battery chemistries", "model": "pro"}', _ctx()
    )

    assert result.ok and result.content == "report"
    name, args = mcp.calls[0]
    assert name == "tavily_research"
    assert args["input"] == "compare EV battery chemistries"
    assert args["model"] == "pro"
    assert "query" not in args


async def test_crawl_and_map_forward_url():
    mcp = FakeMcp()
    runtime = ToolRuntime(_registry(mcp))

    await runtime.execute("tavily_crawl", '{"url": "https://docs.example.com", "max_depth": 2}', _ctx())
    await runtime.execute("tavily_map", '{"url": "https://docs.example.com"}', _ctx())

    assert mcp.calls[0][0] == "tavily_crawl" and mcp.calls[0][1]["url"] == "https://docs.example.com"
    assert mcp.calls[0][1]["max_depth"] == 2
    assert mcp.calls[1][0] == "tavily_map" and mcp.calls[1][1]["url"] == "https://docs.example.com"


async def test_invalid_args_degrade_to_not_ok():
    mcp = FakeMcp()
    runtime = ToolRuntime(_registry(mcp))

    # query is required for search; omitting it is a validation error.
    result = await runtime.execute("tavily_search", '{"max_results": 3}', _ctx())

    assert result.ok is False
    assert "tavily_search" in result.content
    assert mcp.calls == []  # adapter never ran


async def test_mcp_error_degrades_to_not_ok_without_raising():
    mcp = FakeMcp(raises=True)
    runtime = ToolRuntime(_registry(mcp))

    result = await runtime.execute("tavily_search", '{"query": "x"}', _ctx())

    assert result.ok is False
    assert "tavily_search" in result.content


def test_all_five_tools_registered_with_schemas():
    reg = _registry(FakeMcp())
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in reg.build_schemas(ALL_TOOLS)}
    assert set(schemas) == set(ALL_TOOLS)
    assert "query" in schemas["tavily_search"]["properties"]
    assert "urls" in schemas["tavily_extract"]["properties"]
    assert "input" in schemas["tavily_research"]["properties"]


def test_tools_absent_when_not_registered():
    reg = ToolRegistry()  # flag-off path: register_tavily_tools never called
    assert reg.get("tavily_search") is None
    assert reg.build_schemas(ALL_TOOLS) == []


def test_tavily_mcp_sets_bearer_header():
    mcp = TavilyMcp(url="https://mcp.tavily.com/mcp/", api_key="tvly-abc")
    assert mcp._headers == {"Authorization": "Bearer tvly-abc"}
