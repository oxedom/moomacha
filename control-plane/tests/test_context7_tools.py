"""Network-free tests for the Context7 MCP tools.

FakeContext7Mcp records (mcp_tool_name, arguments) per call so we assert that
adapters forward validated args correctly without opening any subprocess.
"""

from dataclasses import dataclass, field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime
from control_plane.runtime.tools.context7 import Context7Mcp, register_context7_tools

ALL_TOOLS = ["context7_resolve_library", "context7_query_docs"]


class FakeMcp:
    def __init__(self, reply: str = "ok", raises: bool = False) -> None:
        self.reply = reply
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.raises:
            raise RuntimeError("context7 down")
        return self.reply


@dataclass
class FakeAgent:
    id: str = "agent-99"
    allowed_tools: list[str] = field(default_factory=lambda: list(ALL_TOOLS))
    is_bastion: bool = False


def _ctx() -> ToolContext:
    return ToolContext(agent=FakeAgent(), zulip=None, channel="sandbox", topic="docs")


def _registry(mcp) -> ToolRegistry:
    reg = ToolRegistry()
    register_context7_tools(reg, mcp)
    return reg


async def test_resolve_forwards_library_name():
    mcp = FakeMcp(reply="[{id: '/vercel/next.js'}]")
    runtime = ToolRuntime(_registry(mcp))

    result = await runtime.execute(
        "context7_resolve_library",
        '{"library_name": "next.js", "query": "middleware"}',
        _ctx(),
    )

    assert result.ok
    name, args = mcp.calls[0]
    assert name == "resolve-library-id"
    assert args["library_name"] == "next.js"
    assert args["query"] == "middleware"


async def test_resolve_omits_empty_query_when_not_set():
    mcp = FakeMcp()
    runtime = ToolRuntime(_registry(mcp))

    result = await runtime.execute(
        "context7_resolve_library",
        '{"library_name": "react"}',
        _ctx(),
    )

    assert result.ok
    _, args = mcp.calls[0]
    # exclude_unset: default empty query not forwarded when LLM didn't set it
    assert "query" not in args


async def test_query_docs_omits_default_tokens_when_not_set():
    mcp = FakeMcp()
    runtime = ToolRuntime(_registry(mcp))

    result = await runtime.execute(
        "context7_query_docs",
        '{"library_id": "/vercel/next.js", "query": "middleware"}',
        _ctx(),
    )

    assert result.ok
    assert mcp.calls[0][1]["library_id"] == "/vercel/next.js"
    # exclude_unset: default tokens=5000 not forwarded when LLM didn't set it
    assert "tokens" not in mcp.calls[0][1]


async def test_query_docs_forwards_library_id_and_tokens():
    mcp = FakeMcp(reply="# Next.js Middleware docs...")
    runtime = ToolRuntime(_registry(mcp))

    result = await runtime.execute(
        "context7_query_docs",
        '{"library_id": "/vercel/next.js", "query": "how to use middleware", "tokens": 8000}',
        _ctx(),
    )

    assert result.ok
    name, args = mcp.calls[0]
    assert name == "get-library-docs"
    assert args["library_id"] == "/vercel/next.js"
    assert args["tokens"] == 8000
    assert args["query"] == "how to use middleware"


async def test_missing_required_field_degrades_to_not_ok():
    mcp = FakeMcp()
    runtime = ToolRuntime(_registry(mcp))

    # library_id is required for query_docs
    result = await runtime.execute(
        "context7_query_docs",
        '{"query": "middleware"}',
        _ctx(),
    )

    assert result.ok is False
    assert mcp.calls == []


async def test_mcp_error_degrades_to_not_ok():
    mcp = FakeMcp(raises=True)
    runtime = ToolRuntime(_registry(mcp))

    result = await runtime.execute(
        "context7_resolve_library",
        '{"library_name": "react"}',
        _ctx(),
    )

    assert result.ok is False
    assert "context7_resolve_library" in result.content


def test_both_tools_registered_with_schemas():
    reg = _registry(FakeMcp())
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in reg.build_schemas(ALL_TOOLS)}
    assert set(schemas) == set(ALL_TOOLS)
    assert "library_name" in schemas["context7_resolve_library"]["properties"]
    assert "library_id" in schemas["context7_query_docs"]["properties"]
    assert "tokens" in schemas["context7_query_docs"]["properties"]


def test_tools_absent_when_not_registered():
    reg = ToolRegistry()
    assert reg.get("context7_resolve_library") is None
    assert reg.build_schemas(ALL_TOOLS) == []


def test_context7_mcp_stores_command():
    mcp = Context7Mcp(command=["npx", "-y", "@upstash/context7-mcp"])
    assert mcp._command == ["npx", "-y", "@upstash/context7-mcp"]
