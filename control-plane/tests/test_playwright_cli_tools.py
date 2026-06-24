from types import SimpleNamespace

from control_plane.runtime.tools.playwright_cli import register_playwright_cli_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime


class FakeCli:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def run(self, session: str, args: list[str]) -> str:
        self.calls.append((session, args))
        return "cli output"


def _runtime(fake: FakeCli) -> ToolRuntime:
    registry = ToolRegistry()
    register_playwright_cli_tools(registry, fake)
    return ToolRuntime(registry)


def _ctx(allowed_tools: list[str], *, session: str | None = "run-1") -> ToolContext:
    agent = SimpleNamespace(
        id="agent 1",
        name="Browser Agent",
        allowed_tools=allowed_tools,
        readable_channels=[],
        is_bastion=False,
    )
    return ToolContext(
        agent=agent,
        zulip=None,
        channel="browser-goals",
        topic="test",
        playwright_session=session,
    )


async def test_browser_open_runs_headed_persistent_named_session():
    fake = FakeCli()
    res = await _runtime(fake).execute(
        "local_browser_open",
        '{"url": "https://example.com"}',
        _ctx(["local_browser_open"]),
    )

    assert res.ok is True
    assert "session=run-1" in res.content
    assert fake.calls == [
        ("run-1", ["open", "https://example.com", "--headed", "--persistent"])
    ]


async def test_browser_actions_use_context_session_and_cli_args():
    fake = FakeCli()
    runtime = _runtime(fake)
    ctx = _ctx(["local_browser_snapshot", "local_browser_click", "local_browser_fill"])

    await runtime.execute("local_browser_snapshot", '{"depth": 2}', ctx)
    await runtime.execute("local_browser_click", '{"ref": "e5", "button": "right"}', ctx)
    await runtime.execute("local_browser_fill", '{"ref": "e7", "text": "hello", "submit": true}', ctx)

    assert fake.calls == [
        ("run-1", ["snapshot", "--depth=2"]),
        ("run-1", ["click", "e5", "right"]),
        ("run-1", ["fill", "e7", "hello", "--submit"]),
    ]


async def test_browser_tool_defaults_to_agent_scoped_session():
    fake = FakeCli()
    res = await _runtime(fake).execute(
        "local_browser_press",
        '{"key": "Enter"}',
        _ctx(["local_browser_press"], session=None),
    )

    assert res.ok is True
    assert fake.calls == [("agent-agent-1", ["press", "Enter"])]


async def test_browser_tools_must_be_allowed_for_agent():
    fake = FakeCli()
    res = await _runtime(fake).execute(
        "local_browser_open",
        '{"url": "https://example.com"}',
        _ctx([]),
    )

    assert res.ok is False
    assert "not permitted" in res.content
    assert fake.calls == []
