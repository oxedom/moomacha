from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

if TYPE_CHECKING:
    from control_plane.runtime.tools.registry import ToolRegistry

logger = logging.getLogger("control_plane")


@dataclass
class ToolResult:
    ok: bool
    content: str  # always a string; fed back to the model as the tool message


@dataclass
class ToolContext:
    """What a tool adapter receives. Extended by later slices (bastion, scheduling)."""

    agent: Any  # ResolvedAgent — duck-typed to avoid a schema import here
    zulip: Any  # AgentClientProtocol
    channel: str
    topic: str
    management: Any = None  # ManagementToolContext | None; set only for bastion turns
    playwright_session: str | None = None  # Browser goal runs bind tools to one CLI session.
    invoking_user: str | None = None  # human sender email; None for scheduled fires (no exec)
    invoking_text: str | None = None  # the human's triggering message (for exec confirm gate)
    source_message_id: int | None = None  # triggering Zulip message id (for the audit log)
    memory_ns: str | None = None  # agent-tier namespace for memory writes/reads; set by the turn loop
    conversation_type: str = "stream"  # "stream" | "direct"; tools that post to a stream guard on this
    direct_recipient_ids: list[int] | None = None  # Zulip user ids for the current direct conversation
    events: Any = None  # EventEmitter | None; set by the turn loop for observability


class ToolRuntime:
    """Validates and dispatches one tool call. Never raises for recoverable failures."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, tool_name: str, raw_args: str, ctx: ToolContext) -> ToolResult:
        """Middleware chokepoint: every tool call from the openai/deepagents runtimes
        passes here. Dispatch, then emit a rich `tool.call` event (args + result)."""
        start = time.monotonic()
        result = await self._dispatch(tool_name, raw_args, ctx)
        if ctx.events is not None:
            await ctx.events.tool_call(
                name=tool_name,
                ok=result.ok,
                args=raw_args,
                result=result.content[:4000],
                latency_ms=int((time.monotonic() - start) * 1000),
                channel=ctx.channel,
                topic=ctx.topic,
                invoking_user=ctx.invoking_user,
                source_message_id=ctx.source_message_id,
                agent_id=str(getattr(ctx.agent, "id", "") or ""),
            )
        return result

    async def _dispatch(self, tool_name: str, raw_args: str, ctx: ToolContext) -> ToolResult:
        # Defense-in-depth: the schema only exposes allowed tools, but execution
        # must not trust that the model stayed inside the schema. Management tools
        # are permitted only for the bastion (privilege follows the is_bastion flag).
        entry = self._registry.get(tool_name)
        if entry is None:
            return ToolResult(ok=False, content=f"Unknown tool '{tool_name}'.")
        # Privilege follows the flag, not the model-editable allowed_tools list:
        # a management/exec tool can never be executed without is_bastion/can_exec,
        # even if the agent's allowed_tools names it.
        is_bastion = getattr(ctx.agent, "is_bastion", False)
        can_exec = getattr(ctx.agent, "can_exec", False)
        if entry.management and not is_bastion:
            return ToolResult(ok=False, content=f"Tool '{tool_name}' is not permitted for this agent.")
        if entry.requires_exec and not can_exec:
            return ToolResult(ok=False, content=f"Tool '{tool_name}' is not permitted for this agent.")
        permitted = (
            tool_name in getattr(ctx.agent, "allowed_tools", [])
            or (entry.management and is_bastion)
            or (entry.requires_exec and can_exec)
        )
        if not permitted:
            return ToolResult(ok=False, content=f"Tool '{tool_name}' is not permitted for this agent.")
        try:
            parsed = entry.input_model.model_validate_json(raw_args)
        except ValidationError as exc:
            return ToolResult(ok=False, content=f"Invalid arguments for '{tool_name}': {exc}")
        try:
            return await entry.adapter(parsed, ctx)
        except Exception as exc:  # noqa: BLE001 - adapter failures must not kill the turn
            logger.exception("Tool '%s' raised", tool_name)
            return ToolResult(ok=False, content=f"Tool '{tool_name}' error: {exc}")
