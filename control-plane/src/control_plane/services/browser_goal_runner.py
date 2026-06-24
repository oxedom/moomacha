from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Literal

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime

BROWSER_TOOL_NAMES = [
    "local_browser_open",
    "local_browser_goto",
    "local_browser_snapshot",
    "local_browser_click",
    "local_browser_fill",
    "local_browser_type",
    "local_browser_press",
    "local_browser_eval",
    "local_browser_show_annotate",
    "local_browser_screenshot",
    "local_browser_close",
]

BrowserGoalState = Literal["running", "paused", "blocked", "done", "failed", "stopped"]

SYSTEM_PROMPT = """You are a supervised headed-browser goal runner.

Use the browser_* tools to work toward the user's goal in a visible Playwright CLI
browser session. Inspect the page with local_browser_snapshot before acting when you
need element refs. Prefer one small browser action at a time. User steering
messages may arrive while you are running; treat the newest steering as
authoritative. If you need the human to point at something in the headed browser,
call local_browser_show_annotate. When the goal is complete, respond with a concise
final result and stop calling tools."""


@dataclass
class BrowserGoalRun:
    id: str
    goal: str
    session: str
    model_id: str
    status: BrowserGoalState
    max_steps: int
    url: str | None = None
    headed: bool = True
    persistent: bool = True
    step: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    transcript: list[dict[str, Any]] = field(default_factory=list)
    steering_queue: list[str] = field(default_factory=list)
    last_output: str | None = None
    last_error: str | None = None
    result: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "session": self.session,
            "model_id": self.model_id,
            "status": self.status,
            "step": self.step,
            "max_steps": self.max_steps,
            "url": self.url,
            "headed": self.headed,
            "persistent": self.persistent,
            "pending_steering": len(self.steering_queue),
            "last_output": self.last_output,
            "last_error": self.last_error,
            "result": self.result,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class BrowserGoalRunner:
    """In-process, cancellable browser goal loop.

    This is intentionally non-durable. It gives humans a useful headed browser
    worker without changing the database schema or queue semantics.
    """

    def __init__(
        self,
        *,
        client_factory,
        llm_api_key: str,
        llm_base_url: str | None,
        default_model: str,
        registry: ToolRegistry,
        runtime: ToolRuntime,
    ) -> None:
        self._client_factory = client_factory
        self._llm_api_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._default_model = default_model
        self._registry = registry
        self._runtime = runtime
        self._runs: dict[str, BrowserGoalRun] = {}

    async def start(
        self,
        *,
        goal: str,
        url: str | None = None,
        model_id: str | None = None,
        max_steps: int = 20,
        headed: bool = True,
        persistent: bool = True,
    ) -> BrowserGoalRun:
        run_id = str(uuid.uuid4())
        run = BrowserGoalRun(
            id=run_id,
            goal=goal,
            session=f"browser-goal-{run_id}",
            model_id=model_id or self._default_model,
            status="running",
            max_steps=max_steps,
            url=url,
            headed=headed,
            persistent=persistent,
            transcript=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _start_message(goal, url)},
            ],
        )
        self._runs[run.id] = run
        run.task = asyncio.create_task(self._run_loop(run))
        return run

    def list(self) -> list[BrowserGoalRun]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def get(self, run_id: str) -> BrowserGoalRun | None:
        return self._runs.get(run_id)

    async def steer(self, run_id: str, message: str) -> BrowserGoalRun | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        run.steering_queue.append(message)
        run.touch()
        return run

    async def pause(self, run_id: str) -> BrowserGoalRun | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        if run.status == "running":
            run.status = "paused"
            run.touch()
            await self._cancel_task(run)
        return run

    async def resume(self, run_id: str) -> BrowserGoalRun | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        if run.status == "paused":
            run.status = "running"
            run.touch()
            run.task = asyncio.create_task(self._run_loop(run))
        return run

    async def stop(self, run_id: str, *, close_browser: bool = False) -> BrowserGoalRun | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        run.status = "stopped"
        run.touch()
        await self._cancel_task(run)
        if close_browser:
            ctx = self._tool_context(run)
            result = await self._runtime.execute("local_browser_close", "{}", ctx)
            run.last_output = result.content
            run.touch()
        return run

    async def aclose(self) -> None:
        await asyncio.gather(
            *(self._cancel_task(run) for run in list(self._runs.values())),
            return_exceptions=True,
        )

    async def _cancel_task(self, run: BrowserGoalRun) -> None:
        task = run.task
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run_loop(self, run: BrowserGoalRun) -> None:
        client = self._client_factory(self._llm_api_key, self._llm_base_url)
        ctx = self._tool_context(run)
        try:
            if run.url and run.step == 0:
                await self._open_initial_url(run, ctx)

            while run.status == "running":
                if run.step >= run.max_steps:
                    run.status = "blocked"
                    run.last_error = f"Reached max_steps={run.max_steps}"
                    run.touch()
                    return

                self._append_steering(run)
                schemas = self._registry.build_schemas(BROWSER_TOOL_NAMES)
                completion = await client.chat.completions.create(
                    model=run.model_id,
                    messages=run.transcript,
                    tools=schemas,
                    temperature=0.2,
                    max_tokens=900,
                )
                msg = completion.choices[0].message
                tool_calls = msg.tool_calls or []
                if not tool_calls:
                    if msg.content is None:
                        raise RuntimeError("LLM returned no text content")
                    run.result = str(msg.content)
                    run.status = "done"
                    run.touch()
                    return

                run.transcript.append(_assistant_message(msg, tool_calls))
                for tc in tool_calls:
                    if run.step >= run.max_steps:
                        run.status = "blocked"
                        run.last_error = f"Reached max_steps={run.max_steps}"
                        run.touch()
                        return
                    result = await self._runtime.execute(
                        tc.function.name,
                        tc.function.arguments,
                        ctx,
                    )
                    run.step += 1
                    run.last_output = result.content
                    run.touch()
                    run.transcript.append(_tool_result_message(tc.id, result.content))
                    if run.status != "running":
                        return
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            if run.status not in {"paused", "stopped"}:
                run.status = "stopped"
                run.touch()
            raise
        except Exception as exc:  # noqa: BLE001 - background runs report failure in status.
            run.status = "failed"
            run.last_error = str(exc)
            run.touch()
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await close()

    async def _open_initial_url(self, run: BrowserGoalRun, ctx: ToolContext) -> None:
        raw_args = json.dumps(
            {
                "url": run.url,
                "headed": run.headed,
                "persistent": run.persistent,
            }
        )
        result = await self._runtime.execute("local_browser_open", raw_args, ctx)
        run.step += 1
        run.last_output = result.content
        run.touch()
        run.transcript.append(
            {
                "role": "assistant",
                "content": f"Initial local_browser_open result:\n{result.content}",
            }
        )

    def _tool_context(self, run: BrowserGoalRun) -> ToolContext:
        agent = SimpleNamespace(
            id=run.id,
            name=f"Browser Goal {run.id}",
            model_id=run.model_id,
            allowed_tools=BROWSER_TOOL_NAMES,
            readable_channels=[],
            is_bastion=False,
        )
        return ToolContext(
            agent=agent,
            zulip=None,
            channel="browser-goals",
            topic=run.id,
            playwright_session=run.session,
        )

    def _append_steering(self, run: BrowserGoalRun) -> None:
        if not run.steering_queue:
            return
        messages = list(run.steering_queue)
        run.steering_queue.clear()
        for message in messages:
            run.transcript.append({"role": "user", "content": f"Human steering: {message}"})
        run.touch()


def _start_message(goal: str, url: str | None) -> str:
    if url:
        return f"Goal: {goal}\nStarting URL: {url}"
    return f"Goal: {goal}"


def _assistant_message(msg: Any, tool_calls: list[Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": msg.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ],
    }


def _tool_result_message(tool_call_id: str, content: str) -> dict[str, str]:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}
