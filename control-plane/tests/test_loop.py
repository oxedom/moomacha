from types import SimpleNamespace

import pytest

from control_plane.runtime.loop import BudgetExceeded, LoopDeps, run_turn
from control_plane.runtime.tools.runtime import ToolResult


def _tool_call(id_: str, name: str, args: str):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=args))


class FakeLLM:
    """Returns scripted assistant messages in order."""

    def __init__(self, scripted: list) -> None:
        self._scripted = list(scripted)
        self.create_calls: list[dict] = []

    @property
    def chat(self):
        return SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        message = self._scripted.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeRegistry:
    def __init__(self, allowed_has_schema: bool = True) -> None:
        self._has = allowed_has_schema

    def build_schemas(self, allowed_tools, is_bastion=False, can_exec=False):
        if allowed_tools and self._has:
            return [{"type": "function", "function": {"name": "read_topic"}}]
        return []


class FakeRuntime:
    def __init__(self) -> None:
        self.executed: list[tuple[str, str]] = []

    async def execute(self, name, raw_args, ctx):
        self.executed.append((name, raw_args))
        return ToolResult(ok=True, content="tool said hi")


def _agent():
    return SimpleNamespace(model_id="gpt-4o", allowed_tools=["read_topic"])


async def test_run_turn_returns_text_when_no_tool_calls():
    final = SimpleNamespace(content="just an answer", tool_calls=None)
    deps = LoopDeps(
        client=FakeLLM([final]), registry=FakeRegistry(),
        runtime=FakeRuntime(), max_tool_calls=10,
    )
    out = await run_turn([{"role": "user", "content": "hi"}], _agent(), object(), deps)
    assert out == "just an answer"


async def test_run_turn_executes_tool_then_answers():
    first = SimpleNamespace(
        content=None,
        tool_calls=[_tool_call("c1", "read_topic", '{"channel": "eng", "topic": "t", "limit": 5}')],
    )
    second = SimpleNamespace(content="answer using tool", tool_calls=None)
    rt = FakeRuntime()
    deps = LoopDeps(
        client=FakeLLM([first, second]), registry=FakeRegistry(),
        runtime=rt, max_tool_calls=10,
    )
    messages = [{"role": "user", "content": "hi"}]
    out = await run_turn(messages, _agent(), object(), deps)
    assert out == "answer using tool"
    assert rt.executed == [("read_topic", '{"channel": "eng", "topic": "t", "limit": 5}')]
    # the loop appended the assistant tool-call turn and the tool result
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in messages)
    assert any(
        m.get("role") == "tool" and m.get("content") == "tool said hi" for m in messages
    )


async def test_run_turn_raises_when_budget_exceeded():
    def asking():
        return SimpleNamespace(
            content=None,
            tool_calls=[_tool_call("c", "read_topic", '{"channel": "eng", "topic": "t", "limit": 1}')],
        )

    deps = LoopDeps(
        client=FakeLLM([asking() for _ in range(5)]), registry=FakeRegistry(),
        runtime=FakeRuntime(), max_tool_calls=3,
    )
    with pytest.raises(BudgetExceeded) as exc_info:
        await run_turn([{"role": "user", "content": "hi"}], _agent(), object(), deps)
    assert exc_info.value.count == 4


async def test_run_turn_invokes_on_tool_call_callback():
    first = SimpleNamespace(
        content=None, tool_calls=[_tool_call("c1", "read_topic", "{}")]
    )
    second = SimpleNamespace(content="done", tool_calls=None)
    seen: list[tuple[str, bool]] = []

    async def on_tool_call(name, ok):
        seen.append((name, ok))

    deps = LoopDeps(
        client=FakeLLM([first, second]), registry=FakeRegistry(),
        runtime=FakeRuntime(), max_tool_calls=10, on_tool_call=on_tool_call,
    )
    await run_turn([{"role": "user", "content": "hi"}], _agent(), object(), deps)
    assert seen == [("read_topic", True)]


async def test_run_turn_emits_llm_call():
    from types import SimpleNamespace
    from control_plane.observability.events import AgentEvent, EventEmitter
    from control_plane.runtime.loop import LoopDeps, run_turn

    captured: list[AgentEvent] = []

    async def sink(ev): captured.append(ev)

    class FakeCompletions:
        async def create(self, **kw):
            msg = SimpleNamespace(content="done", tool_calls=[])
            usage = SimpleNamespace(prompt_tokens=11, completion_tokens=3)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    class FakeRegistry:
        def build_schemas(self, *a, **k): return []

    agent = SimpleNamespace(model_id="gpt-4o", allowed_tools=[], is_bastion=False, can_exec=False)
    em = EventEmitter(trace_id="tr", turn_id="tn", emit_fn=sink)
    deps = LoopDeps(client=client, registry=FakeRegistry(), runtime=None,
                    max_tool_calls=5, events=em)
    out = await run_turn([{"role": "user", "content": "hi"}], agent, None, deps)
    assert out == "done"
    llm = [e for e in captured if e.type == "llm.call"]
    assert llm and llm[0].attrs["model"] == "gpt-4o"
    assert llm[0].attrs["prompt_tokens"] == 11
