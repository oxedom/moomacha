"""`run_command` tool backed by the isolated exec-mcp service over MCP (SSE).

Mirrors ``agent_memory.py``: the control plane is an MCP *client* behind an
injectable ``ExecMcp`` wrapper, so adapters and their tests never touch the
network. Execution itself happens in a separate process (the exec-mcp service),
so no control-plane secret is reachable from a command.

Authorization is enforced HERE, on the consumer side (MCP isolates blast radius,
it does not decide who may exec). All of these must hold or the call is refused:
  - the agent has ``can_exec`` (gated at the registry/runtime layer via the
    ``requires_exec`` tag — privilege follows the flag, not ``allowed_tools``);
  - the invoking channel is in the configured channel allowlist;
  - the invoking human is in the configured user allowlist;
  - when confirm is required, the human's latest message contains "confirm".
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult

logger = logging.getLogger("control_plane")

DEFAULT_TIMEOUT_SECONDS = 65.0
OUTPUT_CAP = 8000
_CONFIRM_RE = re.compile(r"\bconfirm\b", re.IGNORECASE)


def _cap(value: str, limit: int = OUTPUT_CAP) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated to {limit} characters ..."


class ExecMcp:
    """Thin async MCP (SSE) client for the exec-mcp service. Opens a fresh session
    per call (same per-call style as AgentMemoryMcp)."""

    def __init__(
        self, *, url: str, token: str | None = None, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {token}"} if token else None
        self._timeout = timeout_seconds

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        from mcp.client.session import ClientSession
        from mcp.client.sse import sse_client

        try:
            async with sse_client(self._url, headers=self._headers, timeout=self._timeout) as (
                read,
                write,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        except BaseExceptionGroup as eg:
            # anyio TaskGroup wraps the real transport error; unwrap it so the
            # caller (and the agent) sees the actual failure (e.g. ConnectError).
            cause = eg.exceptions[0] if eg.exceptions else eg
            raise cause from eg

        parts = [getattr(b, "text", "") for b in (getattr(result, "content", None) or [])]
        text = "\n".join(p for p in parts if p)
        if getattr(result, "isError", False):
            raise RuntimeError(text or f"MCP tool '{name}' returned an error")
        return text


class RunCommandInput(BaseModel):
    command: str = Field(description="Shell command to run in the project working directory.")


def register_exec_tools(
    registry: ToolRegistry,
    mcp: ExecMcp,
    *,
    channels: list[str],
    users: list[str],
    require_confirm: bool = True,
) -> None:
    """Register the gated ``run_command`` tool (tagged ``requires_exec`` so it is
    available only to a ``can_exec`` agent). The channel/user/confirm gates close
    over config so they cannot be edited by the model."""
    allowed_channels = set(channels)
    allowed_users = set(users)

    async def _run(inp: RunCommandInput, ctx: ToolContext) -> ToolResult:
        if ctx.channel not in allowed_channels:
            return ToolResult(ok=False, content=f"Command execution is not allowed in #{ctx.channel}.")
        if not ctx.invoking_user or ctx.invoking_user not in allowed_users:
            return ToolResult(ok=False, content="You are not authorized to run commands.")
        if require_confirm and not _CONFIRM_RE.search(ctx.invoking_text or ""):
            return ToolResult(
                ok=False,
                content=(
                    "⚠️ Running this command needs confirmation. Reply with a message containing "
                    f"`confirm` to run:\n```\n{inp.command}\n```"
                ),
            )
        logger.warning(
            "EXEC by %s in #%s: %r", ctx.invoking_user, ctx.channel, inp.command
        )
        out = await mcp.call_tool("run_command", {"command": inp.command})
        return ToolResult(ok=True, content=_cap(out))

    registry.register(
        "run_command",
        "Run a shell command in the project working directory (gated; isolated exec service).",
        RunCommandInput,
        _run,
        requires_exec=True,
    )
