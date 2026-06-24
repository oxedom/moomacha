from types import SimpleNamespace

from control_plane.runtime.tools.exec_mcp import ExecMcp, register_exec_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime

CH = "dev"
USER = "alice@example.com"


class FakeExecMcp(ExecMcp):
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return '{"exit_code": 0, "stdout": "ok", "stderr": "", "timed_out": false}'


def _registry(mcp, *, require_confirm=True):
    reg = ToolRegistry()
    register_exec_tools(reg, mcp, channels=[CH], users=[USER], require_confirm=require_confirm)
    return reg


def _ctx(*, channel=CH, user=USER, text="confirm run it", can_exec=True):
    agent = SimpleNamespace(allowed_tools=[], is_bastion=False, can_exec=can_exec)
    return ToolContext(
        agent=agent, zulip=None, channel=channel, topic="t",
        invoking_user=user, invoking_text=text,
    )


def test_run_command_schema_only_for_can_exec():
    reg = _registry(FakeExecMcp())
    assert reg.build_schemas([], can_exec=False) == []
    names = {s["function"]["name"] for s in reg.build_schemas([], can_exec=True)}
    assert names == {"run_command"}


async def test_exec_denied_without_can_exec():
    mcp = FakeExecMcp()
    rt = ToolRuntime(_registry(mcp))
    res = await rt.execute("run_command", '{"command": "ls"}', _ctx(can_exec=False))
    assert res.ok is False and "not permitted" in res.content
    assert mcp.calls == []


async def test_exec_denied_wrong_channel():
    mcp = FakeExecMcp()
    rt = ToolRuntime(_registry(mcp))
    res = await rt.execute("run_command", '{"command": "ls"}', _ctx(channel="random"))
    assert res.ok is False and "not allowed in #random" in res.content
    assert mcp.calls == []


async def test_exec_denied_wrong_user():
    mcp = FakeExecMcp()
    rt = ToolRuntime(_registry(mcp))
    res = await rt.execute("run_command", '{"command": "ls"}', _ctx(user="stranger@x"))
    assert res.ok is False and "not authorized" in res.content
    assert mcp.calls == []


async def test_exec_requires_confirm():
    mcp = FakeExecMcp()
    rt = ToolRuntime(_registry(mcp, require_confirm=True))
    res = await rt.execute("run_command", '{"command": "rm x"}', _ctx(text="please run rm x"))
    assert res.ok is False and "confirm" in res.content.lower()
    assert mcp.calls == []


async def test_exec_runs_when_all_gates_pass():
    mcp = FakeExecMcp()
    rt = ToolRuntime(_registry(mcp))
    res = await rt.execute("run_command", '{"command": "ls"}', _ctx())
    assert res.ok is True and "ok" in res.content
    assert mcp.calls == [("run_command", {"command": "ls"})]


async def test_exec_no_confirm_mode_runs_without_confirm_word():
    mcp = FakeExecMcp()
    rt = ToolRuntime(_registry(mcp, require_confirm=False))
    res = await rt.execute("run_command", '{"command": "ls"}', _ctx(text="just run ls"))
    assert res.ok is True
    assert mcp.calls == [("run_command", {"command": "ls"})]
