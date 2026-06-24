import asyncio
from types import SimpleNamespace

from control_plane.runtime.tools.playwright_cli import register_playwright_cli_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime
from control_plane.services.browser_goal_runner import BrowserGoalRunner


def _tool_call(id_: str, name: str, args: str):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=args))


class FakeLLM:
    def __init__(self, scripted: list) -> None:
        self._scripted = list(scripted)
        self.create_calls: list[dict] = []
        self.closed = False

    @property
    def chat(self):
        return SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        message = self._scripted.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    async def close(self) -> None:
        self.closed = True


class FakeCli:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def run(self, session: str, args: list[str]) -> str:
        self.calls.append((session, args))
        return f"output for {' '.join(args)}"


def _runner(fake_llm: FakeLLM, fake_cli: FakeCli) -> BrowserGoalRunner:
    registry = ToolRegistry()
    register_playwright_cli_tools(registry, fake_cli)
    runtime = ToolRuntime(registry)
    return BrowserGoalRunner(
        client_factory=lambda api_key, base_url: fake_llm,
        llm_api_key="sk-test",
        llm_base_url=None,
        default_model="gpt-test",
        registry=registry,
        runtime=runtime,
    )


async def _wait_for_terminal(runner: BrowserGoalRunner, run_id: str):
    for _ in range(50):
        run = runner.get(run_id)
        if run is not None and run.status in {"blocked", "done", "failed", "stopped"}:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("browser goal did not finish")


async def test_browser_goal_opens_url_runs_tool_loop_and_finishes():
    first = SimpleNamespace(
        content=None,
        tool_calls=[_tool_call("c1", "local_browser_snapshot", '{"depth": 2}')],
    )
    second = SimpleNamespace(content="goal complete", tool_calls=None)
    fake_llm = FakeLLM([first, second])
    fake_cli = FakeCli()
    runner = _runner(fake_llm, fake_cli)

    run = await runner.start(
        goal="check the page",
        url="http://localhost:3000",
        max_steps=5,
    )
    done = await _wait_for_terminal(runner, run.id)

    assert done.status == "done"
    assert done.result == "goal complete"
    assert done.step == 2
    assert fake_cli.calls[0] == (
        done.session,
        ["open", "http://localhost:3000", "--headed", "--persistent"],
    )
    assert fake_cli.calls[1] == (done.session, ["snapshot", "--depth=2"])
    assert fake_llm.closed is True


async def test_browser_goal_blocks_at_max_steps():
    first = SimpleNamespace(
        content=None,
        tool_calls=[_tool_call("c1", "local_browser_snapshot", "{}")],
    )
    fake_llm = FakeLLM([first])
    fake_cli = FakeCli()
    runner = _runner(fake_llm, fake_cli)

    run = await runner.start(goal="loop once", max_steps=0)
    done = await _wait_for_terminal(runner, run.id)

    assert done.status == "blocked"
    assert done.last_error == "Reached max_steps=0"
    assert fake_cli.calls == []


async def test_browser_goal_pause_resume_and_steer():
    first = SimpleNamespace(content="paused run completed", tool_calls=None)
    fake_llm = FakeLLM([first])
    fake_cli = FakeCli()
    runner = _runner(fake_llm, fake_cli)

    run = await runner.start(goal="wait", max_steps=5)
    paused = await runner.pause(run.id)
    assert paused is not None
    await runner.steer(run.id, "Use the test account")
    resumed = await runner.resume(run.id)
    assert resumed is not None
    done = await _wait_for_terminal(runner, run.id)

    assert done.status == "done"
    assert any("Human steering: Use the test account" == m["content"] for m in done.transcript)
