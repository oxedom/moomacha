from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EventType:
    """Closed set of runtime-agnostic agent-event types (string constants)."""
    TURN_START = "turn.start"
    TURN_END = "turn.end"
    LLM_CALL = "llm.call"
    TOOL_CALL = "tool.call"
    SUBAGENT_START = "subagent.start"
    SUBAGENT_END = "subagent.end"
    REASONING = "reasoning"
    PLAN_STEP = "plan.step"
    ERROR = "error"


@dataclass(frozen=True)
class AgentEvent:
    """One thing that happened in a turn. `attrs` carries the type-specific payload."""
    type: str
    trace_id: str
    turn_id: str
    seq: int
    ts: datetime
    attrs: dict[str, Any] = field(default_factory=dict)


EmitFn = Callable[[AgentEvent], Awaitable[None]]


class EventEmitter:
    """Stamps trace_id/turn_id + a monotonic seq onto each event and forwards it.

    One emitter per turn. Convenience methods build the `attrs` payload so callers
    never construct AgentEvent by hand. Emission is best-effort: the underlying sink
    swallows its own failures (see MultiSink), so emit() never breaks a turn.
    """

    def __init__(self, *, trace_id: str, turn_id: str, emit_fn: EmitFn) -> None:
        self._trace_id = trace_id
        self._turn_id = turn_id
        self._emit = emit_fn
        self._seq = 0

    async def _send(self, type_: str, attrs: dict[str, Any]) -> None:
        ev = AgentEvent(
            type=type_, trace_id=self._trace_id, turn_id=self._turn_id,
            seq=self._seq, ts=_now(), attrs=attrs,
        )
        self._seq += 1
        await self._emit(ev)

    async def turn_start(self, **attrs: Any) -> None:
        await self._send(EventType.TURN_START, attrs)

    async def turn_end(self, **attrs: Any) -> None:
        await self._send(EventType.TURN_END, attrs)

    async def llm_call(self, **attrs: Any) -> None:
        await self._send(EventType.LLM_CALL, attrs)

    async def tool_call(self, **attrs: Any) -> None:
        await self._send(EventType.TOOL_CALL, attrs)

    async def error(self, **attrs: Any) -> None:
        await self._send(EventType.ERROR, attrs)

    # codex-ready extension points (no caller in slice 1):
    async def reasoning(self, **attrs: Any) -> None:
        await self._send(EventType.REASONING, attrs)

    async def plan_step(self, **attrs: Any) -> None:
        await self._send(EventType.PLAN_STEP, attrs)
