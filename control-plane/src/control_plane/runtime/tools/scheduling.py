"""Agent-callable scheduling tools (sub-project #3, Task 8).

Three thin adapters over ``ScheduleStore`` — ``schedule_task`` (create a one-shot
or recurring schedule), ``list_my_schedules`` and ``cancel_schedule``. They register
against #1's ``ToolRuntime`` seam.

Design (docs/superpowers/specs/2026-05-24-scheduling-design.md §6):

- The ``ScheduleStore`` (and a ``clock``) are injected by closure at registration —
  mirrors ``register_agent_memory_tools`` in ``agent_memory.py`` — so the adapters and
  their unit tests never touch app wiring or the shared ``ToolContext`` dataclass.
- Target is implicit: a schedule always fires into the agent's CURRENT
  ``ctx.(channel, topic)``; there is no cross-topic scheduling in v1. ``list``/``cancel``
  are likewise scoped to ``ctx.(channel, topic)`` (topic-scoped ownership, D2).
- The store emits ``schedule_created`` / ``schedule_cancelled`` itself, so these
  adapters MUST NOT re-emit them.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult
from control_plane.services.schedule_store import ScheduleStore

INSTRUCTION_PREVIEW = 80


def _now_utc() -> datetime:
    return datetime.now(UTC)


# --- Agent-facing input models (channel/topic come from ctx, never the model) ---


class ScheduleTaskInput(BaseModel):
    instruction: str = Field(
        description="What to do when the schedule fires; sent as the agent's invocation message."
    )
    cron: str | None = Field(
        default=None,
        description="Recurring 5-field cron expression (e.g. '0 9 * * *'). Mutually exclusive with run_at/delay_seconds.",
    )
    run_at: datetime | None = Field(
        default=None,
        description="One-shot absolute time (ISO 8601). Naive times are read in `timezone`. Mutually exclusive with cron/delay_seconds.",
    )
    delay_seconds: int | None = Field(
        default=None,
        ge=1,
        description="One-shot relative delay in seconds from now. Mutually exclusive with cron/run_at.",
    )
    timezone: str = Field(
        default="UTC", description="IANA timezone for cron evaluation and naive run_at."
    )


class ListMySchedulesInput(BaseModel):
    include_inactive: bool = Field(
        default=False, description="Include cancelled/completed/missed schedules, not just active."
    )


class CancelScheduleInput(BaseModel):
    schedule_id: str = Field(description="The UUID of the schedule to cancel (from list_my_schedules).")


# --- Adapters --------------------------------------------------------------


async def _schedule_task(inp: ScheduleTaskInput, ctx: ToolContext, store: ScheduleStore, clock: Callable[[], datetime]) -> ToolResult:
    timing = [("cron", inp.cron), ("run_at", inp.run_at), ("delay_seconds", inp.delay_seconds)]
    set_args = [name for name, value in timing if value is not None]
    if len(set_args) != 1:
        return ToolResult(
            ok=False,
            content="Provide exactly one of cron, run_at, or delay_seconds "
            f"(got {set_args or 'none'}).",
        )

    try:
        tz = ZoneInfo(inp.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ToolResult(ok=False, content=f"Unknown timezone '{inp.timezone}'.")

    agent_id: uuid.UUID = ctx.agent.id
    now = clock()

    if inp.cron is not None:
        if not croniter.is_valid(inp.cron):
            return ToolResult(ok=False, content=f"Invalid cron expression '{inp.cron}'.")
        row = await store.create_recurring(
            agent_id=agent_id, channel=ctx.channel, topic=ctx.topic,
            instruction=inp.instruction, cron=inp.cron, timezone=inp.timezone, now=now,
        )
    else:
        if inp.delay_seconds is not None:
            run_at = now + timedelta(seconds=inp.delay_seconds)
        else:
            run_at = inp.run_at  # type: ignore[assignment]
            run_at = run_at.replace(tzinfo=tz) if run_at.tzinfo is None else run_at
            run_at = run_at.astimezone(UTC)
        row = await store.create_one_shot(
            agent_id=agent_id, channel=ctx.channel, topic=ctx.topic,
            instruction=inp.instruction, run_at=run_at,
        )

    return ToolResult(
        ok=True,
        content=f"Scheduled {row.kind} (id={row.id}); next run {row.next_run_at.isoformat()}.",
    )


async def _list_my_schedules(inp: ListMySchedulesInput, ctx: ToolContext, store: ScheduleStore) -> ToolResult:
    rows = await store.list_for_topic(ctx.channel, ctx.topic, include_inactive=inp.include_inactive)
    if not rows:
        return ToolResult(ok=True, content="No schedules in this topic.")
    lines = []
    for r in rows:
        when = r.cron_expression if r.kind == "recurring" else r.run_at.isoformat()
        instr = r.instruction if len(r.instruction) <= INSTRUCTION_PREVIEW else r.instruction[:INSTRUCTION_PREVIEW] + "…"
        lines.append(
            f"- {r.id} [{r.kind}, {r.status}] {when} (next {r.next_run_at.isoformat()}): {instr}"
        )
    return ToolResult(ok=True, content="\n".join(lines))


async def _cancel_schedule(inp: CancelScheduleInput, ctx: ToolContext, store: ScheduleStore) -> ToolResult:
    try:
        schedule_id = uuid.UUID(inp.schedule_id)
    except ValueError:
        return ToolResult(ok=False, content=f"'{inp.schedule_id}' is not a valid schedule id.")
    cancelled = await store.cancel(schedule_id, ctx.channel, ctx.topic)
    if not cancelled:
        return ToolResult(
            ok=False,
            content="No active schedule with that id in this topic (already cancelled, or not yours).",
        )
    return ToolResult(ok=True, content=f"Cancelled schedule {schedule_id}.")


def register_scheduling_tools(
    registry: ToolRegistry, store: ScheduleStore, clock: Callable[[], datetime] = _now_utc
) -> None:
    """Register the three scheduling tools. Called at app startup when scheduling
    tools are exposed; absent from the schema otherwise."""
    registry.register(
        "schedule_task",
        "Schedule a future task in THIS topic: one-shot (run_at/delay_seconds) or recurring (cron). Fires the given instruction back to you.",
        ScheduleTaskInput,
        lambda inp, ctx: _schedule_task(inp, ctx, store, clock),
    )
    registry.register(
        "list_my_schedules",
        "List the schedules in THIS topic (active only unless include_inactive).",
        ListMySchedulesInput,
        lambda inp, ctx: _list_my_schedules(inp, ctx, store),
    )
    registry.register(
        "cancel_schedule",
        "Cancel an active schedule in THIS topic by its id.",
        CancelScheduleInput,
        lambda inp, ctx: _cancel_schedule(inp, ctx, store),
    )
