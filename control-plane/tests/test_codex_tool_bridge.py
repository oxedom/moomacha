import asyncio
import contextlib
import json

import pytest
import uvicorn
from pydantic import BaseModel

from control_plane.runtime.runners.codex_tool_bridge import BridgeSession, CodexToolBridge
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolResult


class _FakeRuntime:
    def __init__(self):
        self.calls = []
        self.result = ToolResult(ok=True, content="dispatched")

    async def execute(self, name, raw_args, ctx):
        self.calls.append((name, raw_args, ctx))
        return self.result


def test_mint_returns_unique_tokens_and_resolves():
    bridge = CodexToolBridge(ToolRegistry())
    rt = _FakeRuntime()
    t1 = bridge.mint(ctx="ctxA", runtime=rt, allowed=["read_topic"])
    t2 = bridge.mint(ctx="ctxB", runtime=rt, allowed=["read_topic"])
    assert t1 != t2
    assert isinstance(bridge._sessions[t1], BridgeSession)
    assert bridge._sessions[t1].ctx == "ctxA"


def test_release_removes_session_and_is_idempotent():
    bridge = CodexToolBridge(ToolRegistry())
    t = bridge.mint(ctx="c", runtime=_FakeRuntime(), allowed=[])
    bridge.release(t)
    assert t not in bridge._sessions
    bridge.release(t)  # no raise on double release


@pytest.mark.asyncio
async def test_dispatch_unknown_token_errors():
    bridge = CodexToolBridge(ToolRegistry())
    with pytest.raises(PermissionError):
        await bridge._dispatch("nope-token", "read_topic", {})


@pytest.mark.asyncio
async def test_dispatch_tool_not_allowed_errors():
    bridge = CodexToolBridge(ToolRegistry())
    t = bridge.mint(ctx="c", runtime=_FakeRuntime(), allowed=["read_topic"])
    with pytest.raises(PermissionError):
        await bridge._dispatch(t, "gtasks_list", {})


@pytest.mark.asyncio
async def test_dispatch_calls_runtime_with_bound_ctx_and_json_args():
    rt = _FakeRuntime()
    bridge = CodexToolBridge(ToolRegistry())
    t = bridge.mint(ctx="the-ctx", runtime=rt, allowed=["read_topic"])
    out = await bridge._dispatch(t, "read_topic", {"limit": 5})
    assert out == "dispatched"
    name, raw_args, ctx = rt.calls[0]
    assert name == "read_topic"          #  prefix stripped
    assert ctx == "the-ctx"
    assert json.loads(raw_args) == {"limit": 5}


class _EchoArgs(BaseModel):
    text: str


@contextlib.asynccontextmanager
async def _serve(app, host="127.0.0.1", port=9119):
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        yield f"http://{host}:{port}/mcp"
    finally:
        server.should_exit = True
        await task


@pytest.mark.asyncio
async def test_bridge_app_lists_and_calls_tools_with_bearer_token():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    registry = ToolRegistry()

    async def _adapter(parsed, ctx):
        return ToolResult(ok=True, content=f"echo:{parsed.text}")

    registry.register("echo", "Echo text back", _EchoArgs, _adapter)

    bridge = CodexToolBridge(registry)
    rt = _FakeRuntime()
    rt.result = ToolResult(ok=True, content="echo:hi")
    token = bridge.mint(ctx="c", runtime=rt, allowed=["echo"])

    async with _serve(bridge.app()) as url:
        # wrong token -> list rejected
        async with streamablehttp_client(url, headers={"Authorization": "Bearer wrong"}) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                with pytest.raises(Exception):
                    await session.list_tools()
        # right token -> sees echo and can call it
        async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert any(t.name == "echo" for t in tools.tools)
                res = await session.call_tool("echo", {"text": "hi"})
                assert "echo:hi" in res.content[0].text
    assert rt.calls and rt.calls[0][0] == "echo"
