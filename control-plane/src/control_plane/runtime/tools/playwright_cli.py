from __future__ import annotations

import asyncio
import re
from typing import Literal

from pydantic import BaseModel, Field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult

DEFAULT_TIMEOUT_SECONDS = 30
OUTPUT_CAP = 12_000
SESSION_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class PlaywrightCliError(RuntimeError):
    pass


class PlaywrightCli:
    """Small async wrapper around the local playwright-cli binary."""

    def __init__(
        self,
        binary: str = "playwright-cli",
        *,
        default_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        output_cap: int = OUTPUT_CAP,
    ) -> None:
        self._binary = binary
        self._default_timeout_seconds = default_timeout_seconds
        self._output_cap = output_cap

    async def run(
        self,
        session: str,
        args: list[str],
        *,
        timeout_seconds: int | None = None,
    ) -> str:
        timeout = timeout_seconds or self._default_timeout_seconds
        command = [self._binary, f"-s={session}", *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise PlaywrightCliError(
                "playwright-cli was not found on PATH for this process"
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise PlaywrightCliError(
                f"playwright-cli timed out after {timeout} seconds"
            ) from exc
        except asyncio.CancelledError:
            proc.kill()
            await proc.communicate()
            raise

        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        combined = "\n".join(part for part in [out, err] if part)
        if proc.returncode != 0:
            raise PlaywrightCliError(
                combined or f"playwright-cli exited with code {proc.returncode}"
            )
        return _cap(combined, self._output_cap)


class BrowserOpenInput(BaseModel):
    url: str | None = Field(default=None, description="URL to open. Omit for a blank page.")
    headed: bool = Field(default=True, description="Show the browser window.")
    persistent: bool = Field(default=True, description="Keep cookies/profile data for the session.")
    browser: Literal["chromium", "chrome", "firefox", "webkit", "msedge"] | None = Field(
        default=None,
        description="Optional browser engine/channel.",
    )
    profile: str | None = Field(
        default=None,
        description="Optional profile path for a persistent browser profile.",
    )


class BrowserGotoInput(BaseModel):
    url: str = Field(description="URL to navigate the current page to.")


class BrowserSnapshotInput(BaseModel):
    ref: str | None = Field(default=None, description="Optional element ref to snapshot.")
    depth: int | None = Field(default=None, ge=1, le=10, description="Optional snapshot depth.")
    filename: str | None = Field(default=None, description="Optional file path for snapshot output.")


class BrowserClickInput(BaseModel):
    ref: str = Field(description="Element ref from local_browser_snapshot.")
    button: Literal["left", "right", "middle"] | None = Field(
        default=None,
        description="Optional mouse button.",
    )


class BrowserFillInput(BaseModel):
    ref: str = Field(description="Editable element ref from local_browser_snapshot.")
    text: str = Field(description="Text to fill into the element.")
    submit: bool = Field(default=False, description="Press Enter after filling.")


class BrowserTypeInput(BaseModel):
    text: str = Field(description="Text to type into the currently focused element.")


class BrowserPressInput(BaseModel):
    key: str = Field(description="Keyboard key, for example Enter, Escape, or ArrowDown.")


class BrowserEvalInput(BaseModel):
    expression: str = Field(description="JavaScript expression or function to evaluate.")
    ref: str | None = Field(default=None, description="Optional element ref to evaluate against.")


class BrowserShowAnnotateInput(BaseModel):
    annotate: bool = Field(
        default=True,
        description="Ask the headed browser to display interactive annotations.",
    )


class BrowserScreenshotInput(BaseModel):
    ref: str | None = Field(default=None, description="Optional element ref to screenshot.")
    filename: str | None = Field(default=None, description="Optional image output path.")


class BrowserCloseInput(BaseModel):
    pass


def _cap(value: str, limit: int = OUTPUT_CAP) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated to {limit} characters ..."


def _session_from_context(ctx: ToolContext) -> str:
    explicit = getattr(ctx, "playwright_session", None)
    if explicit:
        return _sanitize_session(explicit)
    agent_id = getattr(ctx.agent, "id", None)
    if agent_id is not None:
        return _sanitize_session(f"agent-{agent_id}")
    agent_name = getattr(ctx.agent, "name", None)
    if agent_name:
        return _sanitize_session(f"agent-{agent_name}")
    return "agent-browser"


def _sanitize_session(value: str) -> str:
    session = SESSION_RE.sub("-", value).strip("-")
    return session[:80] or "agent-browser"


def _result(session: str, args: list[str], output: str) -> ToolResult:
    rendered = " ".join(args)
    body = output or "(no output)"
    return ToolResult(ok=True, content=f"session={session}\ncommand={rendered}\n{body}")


async def _run(cli: PlaywrightCli, ctx: ToolContext, args: list[str]) -> ToolResult:
    session = _session_from_context(ctx)
    output = await cli.run(session, args)
    return _result(session, args, output)


async def local_browser_open(inp: BrowserOpenInput, ctx: ToolContext, cli: PlaywrightCli) -> ToolResult:
    args = ["open"]
    if inp.url:
        args.append(inp.url)
    if inp.headed:
        args.append("--headed")
    if inp.persistent:
        args.append("--persistent")
    if inp.browser:
        args.append(f"--browser={inp.browser}")
    if inp.profile:
        args.append(f"--profile={inp.profile}")
    return await _run(cli, ctx, args)


async def local_browser_goto(inp: BrowserGotoInput, ctx: ToolContext, cli: PlaywrightCli) -> ToolResult:
    return await _run(cli, ctx, ["goto", inp.url])


async def local_browser_snapshot(
    inp: BrowserSnapshotInput, ctx: ToolContext, cli: PlaywrightCli
) -> ToolResult:
    args = ["snapshot"]
    if inp.ref:
        args.append(inp.ref)
    if inp.depth is not None:
        args.append(f"--depth={inp.depth}")
    if inp.filename:
        args.append(f"--filename={inp.filename}")
    return await _run(cli, ctx, args)


async def local_browser_click(inp: BrowserClickInput, ctx: ToolContext, cli: PlaywrightCli) -> ToolResult:
    args = ["click", inp.ref]
    if inp.button:
        args.append(inp.button)
    return await _run(cli, ctx, args)


async def local_browser_fill(inp: BrowserFillInput, ctx: ToolContext, cli: PlaywrightCli) -> ToolResult:
    args = ["fill", inp.ref, inp.text]
    if inp.submit:
        args.append("--submit")
    return await _run(cli, ctx, args)


async def local_browser_type(inp: BrowserTypeInput, ctx: ToolContext, cli: PlaywrightCli) -> ToolResult:
    return await _run(cli, ctx, ["type", inp.text])


async def local_browser_press(inp: BrowserPressInput, ctx: ToolContext, cli: PlaywrightCli) -> ToolResult:
    return await _run(cli, ctx, ["press", inp.key])


async def local_browser_eval(inp: BrowserEvalInput, ctx: ToolContext, cli: PlaywrightCli) -> ToolResult:
    args = ["eval", inp.expression]
    if inp.ref:
        args.append(inp.ref)
    return await _run(cli, ctx, args)


async def local_browser_show_annotate(
    inp: BrowserShowAnnotateInput, ctx: ToolContext, cli: PlaywrightCli
) -> ToolResult:
    args = ["show"]
    if inp.annotate:
        args.append("--annotate")
    return await _run(cli, ctx, args)


async def local_browser_screenshot(
    inp: BrowserScreenshotInput, ctx: ToolContext, cli: PlaywrightCli
) -> ToolResult:
    args = ["screenshot"]
    if inp.ref:
        args.append(inp.ref)
    if inp.filename:
        args.append(f"--filename={inp.filename}")
    return await _run(cli, ctx, args)


async def local_browser_close(inp: BrowserCloseInput, ctx: ToolContext, cli: PlaywrightCli) -> ToolResult:
    return await _run(cli, ctx, ["close"])


def register_playwright_cli_tools(registry: ToolRegistry, cli: PlaywrightCli) -> None:
    registry.register(
        "local_browser_open",
        "Open a headed Playwright CLI browser session. The session is owned by the current agent/run.",
        BrowserOpenInput,
        lambda inp, ctx: local_browser_open(inp, ctx, cli),
    )
    registry.register(
        "local_browser_goto",
        "Navigate the current Playwright CLI browser session to a URL.",
        BrowserGotoInput,
        lambda inp, ctx: local_browser_goto(inp, ctx, cli),
    )
    registry.register(
        "local_browser_snapshot",
        "Capture the current page accessibility snapshot and element refs.",
        BrowserSnapshotInput,
        lambda inp, ctx: local_browser_snapshot(inp, ctx, cli),
    )
    registry.register(
        "local_browser_click",
        "Click an element ref from local_browser_snapshot.",
        BrowserClickInput,
        lambda inp, ctx: local_browser_click(inp, ctx, cli),
    )
    registry.register(
        "local_browser_fill",
        "Fill an editable element ref from local_browser_snapshot.",
        BrowserFillInput,
        lambda inp, ctx: local_browser_fill(inp, ctx, cli),
    )
    registry.register(
        "local_browser_type",
        "Type text into the currently focused element.",
        BrowserTypeInput,
        lambda inp, ctx: local_browser_type(inp, ctx, cli),
    )
    registry.register(
        "local_browser_press",
        "Press a keyboard key in the headed browser.",
        BrowserPressInput,
        lambda inp, ctx: local_browser_press(inp, ctx, cli),
    )
    registry.register(
        "local_browser_eval",
        "Evaluate JavaScript in the current page, optionally against an element ref.",
        BrowserEvalInput,
        lambda inp, ctx: local_browser_eval(inp, ctx, cli),
    )
    registry.register(
        "local_browser_show_annotate",
        "Show browser annotations so the human can point at or inspect elements.",
        BrowserShowAnnotateInput,
        lambda inp, ctx: local_browser_show_annotate(inp, ctx, cli),
    )
    registry.register(
        "local_browser_screenshot",
        "Take a screenshot of the page or an element.",
        BrowserScreenshotInput,
        lambda inp, ctx: local_browser_screenshot(inp, ctx, cli),
    )
    registry.register(
        "local_browser_close",
        "Close the current Playwright CLI browser page.",
        BrowserCloseInput,
        lambda inp, ctx: local_browser_close(inp, ctx, cli),
    )
