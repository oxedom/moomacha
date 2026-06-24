import pytest
from control_plane.runtime.runners.router import AgentRunnerRouter
from control_plane.runtime.runners.base import UnknownRuntimeKind
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime


class _Agent:
    def __init__(self, kind):
        self.runtime_kind = kind


class _Fake:
    async def run(self, inp):
        return "x"


def test_default_router_selects_openai_for_missing_kind():
    registry = ToolRegistry()
    router = AgentRunnerRouter.default(registry, ToolRuntime(registry), max_tool_calls=10)

    class _NoKind:  # legacy row without the attribute
        pass

    runner = router.select(_NoKind())
    assert type(runner).__name__ == "OpenAIToolLoopRunner"


def test_router_selects_openai_for_explicit_default():
    registry = ToolRegistry()
    router = AgentRunnerRouter.default(registry, ToolRuntime(registry), max_tool_calls=10)
    runner = router.select(_Agent("openai_tool_loop"))
    assert type(runner).__name__ == "OpenAIToolLoopRunner"


def test_router_selects_registered_deepagents_runner():
    fake = _Fake()
    router = AgentRunnerRouter({"openai_tool_loop": _Fake(), "deepagents": fake})
    assert router.select(_Agent("deepagents")) is fake


def test_unknown_kind_fails_closed():
    router = AgentRunnerRouter({"openai_tool_loop": _Fake()})
    with pytest.raises(UnknownRuntimeKind):
        router.select(_Agent("wat"))


def test_select_codex_runner():
    from control_plane.runtime.runners.codex_runner import CodexRunner

    class _A:
        runtime_kind = "codex"

    runner = CodexRunner(workspaces=None, openai_key="k")
    router = AgentRunnerRouter({"openai_tool_loop": _Fake(), "codex": runner})
    assert router.select(_A()) is runner


def test_relay_runner_selectable_when_registered():
    from control_plane.runtime.runners.relay_runner import RelayRunner

    relay = RelayRunner()
    router = AgentRunnerRouter({"openai_tool_loop": _Fake(), "relay": relay})
    assert router.select(_Agent("relay")) is relay


def test_relay_fails_closed_when_not_registered():
    # Flag off => "relay" key absent => fail closed, exactly like an unknown kind.
    router = AgentRunnerRouter({"openai_tool_loop": _Fake()})
    with pytest.raises(UnknownRuntimeKind):
        router.select(_Agent("relay"))
