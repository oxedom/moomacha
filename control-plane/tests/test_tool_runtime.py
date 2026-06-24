from types import SimpleNamespace

from pydantic import BaseModel

from control_plane.runtime.tools.runtime import ToolContext, ToolResult, ToolRuntime


class _In(BaseModel):
    n: int


_executed: list[int] = []


async def _adapter(inp: _In, ctx: ToolContext) -> ToolResult:
    _executed.append(inp.n)
    return ToolResult(ok=True, content=f"got {inp.n}")


class FakeRegistry:
    """Minimal stand-in for ToolRegistry (built in Task 2)."""

    def __init__(self, name: str, input_model, adapter) -> None:
        self._entry = SimpleNamespace(
            input_model=input_model, adapter=adapter, management=False, requires_exec=False
        )
        self._name = name

    def get(self, name: str):
        return self._entry if name == self._name else None


def _ctx(allowed: list[str]) -> ToolContext:
    agent = SimpleNamespace(allowed_tools=allowed, readable_channels=[])
    return ToolContext(agent=agent, zulip=None, channel="c", topic="t")


async def test_execute_valid_args_runs_adapter():
    rt = ToolRuntime(FakeRegistry("t1", _In, _adapter))
    res = await rt.execute("t1", '{"n": 5}', _ctx(["t1"]))
    assert res.ok is True
    assert res.content == "got 5"


async def test_execute_not_allowed_skips_adapter():
    before = len(_executed)
    rt = ToolRuntime(FakeRegistry("t1", _In, _adapter))
    res = await rt.execute("t1", '{"n": 5}', _ctx([]))
    assert res.ok is False
    assert "not permitted" in res.content.lower()
    assert len(_executed) == before


async def test_execute_unknown_tool():
    rt = ToolRuntime(FakeRegistry("t1", _In, _adapter))
    res = await rt.execute("nope", "{}", _ctx(["nope"]))
    assert res.ok is False
    assert "unknown tool" in res.content.lower()


async def test_execute_invalid_args_skips_adapter():
    before = len(_executed)
    rt = ToolRuntime(FakeRegistry("t1", _In, _adapter))
    res = await rt.execute("t1", '{"n": "not-an-int"}', _ctx(["t1"]))
    assert res.ok is False
    assert "invalid arguments" in res.content.lower()
    assert len(_executed) == before


async def test_execute_adapter_error_is_wrapped():
    async def boom(inp: _In, ctx: ToolContext) -> ToolResult:
        raise RuntimeError("kaboom")

    rt = ToolRuntime(FakeRegistry("t1", _In, boom))
    res = await rt.execute("t1", '{"n": 1}', _ctx(["t1"]))
    assert res.ok is False
    assert "kaboom" in res.content


async def test_execute_permits_management_tool_only_for_bastion():
    from pydantic import BaseModel
    from control_plane.runtime.tools.registry import ToolRegistry
    from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime

    class Args(BaseModel):
        name: str

    async def adapter(parsed, ctx):
        return ToolResult(ok=True, content=f"ran for {parsed.name}")

    reg = ToolRegistry()
    reg.register("delete_agent", "del", Args, adapter, management=True)
    runtime = ToolRuntime(reg)
    agent = SimpleNamespace(allowed_tools=[], readable_channels=[], is_bastion=False)
    ctx = ToolContext(agent=agent, zulip=None, channel="c", topic="t")

    denied = await runtime.execute("delete_agent", '{"name": "x"}', ctx)
    assert denied.ok is False and "not permitted" in denied.content

    agent.is_bastion = True
    allowed = await runtime.execute("delete_agent", '{"name": "x"}', ctx)
    assert allowed.ok is True and "ran for x" in allowed.content


async def test_execute_denies_privileged_tool_named_in_allowed_without_flag():
    # Defense-in-depth: a management/exec tool named in the model-editable
    # allowed_tools must NOT be executable without the is_bastion/can_exec flag.
    from pydantic import BaseModel
    from control_plane.runtime.tools.registry import ToolRegistry
    from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime

    class Args(BaseModel):
        name: str

    async def adapter(parsed, ctx):
        return ToolResult(ok=True, content="ran")

    reg = ToolRegistry()
    reg.register("delete_agent", "del", Args, adapter, management=True)
    reg.register("run_command", "shell", Args, adapter, requires_exec=True)
    runtime = ToolRuntime(reg)
    agent = SimpleNamespace(
        allowed_tools=["delete_agent", "run_command"],
        readable_channels=[],
        is_bastion=False,
        can_exec=False,
    )
    ctx = ToolContext(agent=agent, zulip=None, channel="c", topic="t")

    mgmt = await runtime.execute("delete_agent", '{"name": "x"}', ctx)
    assert mgmt.ok is False and "not permitted" in mgmt.content

    exec_denied = await runtime.execute("run_command", '{"name": "x"}', ctx)
    assert exec_denied.ok is False and "not permitted" in exec_denied.content


def test_tool_context_management_defaults_none():
    from control_plane.runtime.tools.runtime import ToolContext

    ctx = ToolContext(agent=object(), zulip=None, channel="c", topic="t")
    assert ctx.management is None


async def test_execute_emits_tool_call_event():
    from control_plane.observability.events import AgentEvent, EventEmitter

    captured: list[AgentEvent] = []

    async def sink(ev: AgentEvent) -> None:
        captured.append(ev)

    em = EventEmitter(trace_id="tr", turn_id="tn", emit_fn=sink)
    ctx = _ctx(["t1"])
    ctx.events = em
    rt = ToolRuntime(FakeRegistry("t1", _In, _adapter))
    await rt.execute("t1", '{"n": 5}', ctx)

    assert len(captured) == 1
    ev = captured[0]
    assert ev.type == "tool.call"
    assert ev.attrs["name"] == "t1" and ev.attrs["ok"] is True
    assert ev.attrs["args"] == '{"n": 5}'
    assert "latency_ms" in ev.attrs
