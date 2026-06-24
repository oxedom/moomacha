"""Network-free tests for the agent-callable scheduling tools.

Per the design spec §8, the adapters are exercised over a REAL ScheduleStore on
sqlite (not a mock) plus a fake ctx + fixed clock, so we assert real row state
and event emission. The store owns schedule_created/schedule_cancelled events;
the tools must NOT re-emit them.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import ScheduledJobRow
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime
from control_plane.runtime.tools.scheduling import register_scheduling_tools
from control_plane.services.schedule_store import ScheduleStore

TOOLS = ["schedule_task", "list_my_schedules", "cancel_schedule"]


def _now():
    return datetime(2026, 5, 24, 12, 0, tzinfo=UTC)


@dataclass
class FakeAgent:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    allowed_tools: list[str] = field(default_factory=lambda: list(TOOLS))
    is_bastion: bool = False


def _ctx(channel="sandbox", topic="Project X", agent=None) -> ToolContext:
    return ToolContext(agent=agent or FakeAgent(), zulip=None, channel=channel, topic=topic)


async def _harness():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    events: list[dict] = []

    async def fake_write_event(**kwargs):
        events.append(kwargs)

    store = ScheduleStore(factory, fake_write_event)
    reg = ToolRegistry()
    register_scheduling_tools(reg, store, clock=_now)
    return ToolRuntime(reg), store, factory, engine, events


async def _rows(factory) -> list[ScheduledJobRow]:
    async with factory() as session:
        return list((await session.execute(select(ScheduledJobRow))).scalars())


async def test_schedule_task_cron_creates_recurring_row():
    runtime, store, factory, engine, events = await _harness()
    try:
        result = await runtime.execute(
            "schedule_task",
            '{"instruction": "standup", "cron": "0 9 * * *", "timezone": "UTC"}',
            _ctx(),
        )
        assert result.ok
        rows = await _rows(factory)
        assert len(rows) == 1
        row = rows[0]
        assert row.kind == "recurring"
        assert row.cron_expression == "0 9 * * *"
        # next 09:00 UTC strictly after 2026-05-24 12:00 is 2026-05-25 09:00
        assert row.next_run_at == datetime(2026, 5, 25, 9, 0, tzinfo=UTC)
        assert str(row.id) in result.content
        # The STORE emits schedule_created exactly once; the tool must not re-emit.
        assert sum(e["event_type"] == "schedule_created" for e in events) == 1
    finally:
        await engine.dispose()


async def test_schedule_task_run_at_creates_one_shot():
    runtime, store, factory, engine, events = await _harness()
    try:
        result = await runtime.execute(
            "schedule_task",
            '{"instruction": "ping", "run_at": "2026-05-24T15:00:00+00:00"}',
            _ctx(),
        )
        assert result.ok
        rows = await _rows(factory)
        assert len(rows) == 1
        assert rows[0].kind == "one_shot"
        assert rows[0].next_run_at == datetime(2026, 5, 24, 15, 0, tzinfo=UTC)
    finally:
        await engine.dispose()


async def test_schedule_task_delay_seconds_creates_one_shot_relative_to_clock():
    runtime, store, factory, engine, events = await _harness()
    try:
        result = await runtime.execute(
            "schedule_task",
            '{"instruction": "in two hours", "delay_seconds": 7200}',
            _ctx(),
        )
        assert result.ok
        rows = await _rows(factory)
        assert rows[0].kind == "one_shot"
        assert rows[0].next_run_at == _now() + timedelta(hours=2)
    finally:
        await engine.dispose()


async def test_schedule_task_naive_run_at_interpreted_in_timezone():
    runtime, store, factory, engine, events = await _harness()
    try:
        result = await runtime.execute(
            "schedule_task",
            '{"instruction": "morning", "run_at": "2026-05-25T09:00:00", "timezone": "America/New_York"}',
            _ctx(),
        )
        assert result.ok
        rows = await _rows(factory)
        # 09:00 EDT (UTC-4) == 13:00 UTC
        assert rows[0].next_run_at == datetime(2026, 5, 25, 13, 0, tzinfo=UTC)
    finally:
        await engine.dispose()


async def test_schedule_task_zero_timing_args_fails_with_no_row():
    runtime, store, factory, engine, events = await _harness()
    try:
        result = await runtime.execute("schedule_task", '{"instruction": "x"}', _ctx())
        assert result.ok is False
        assert await _rows(factory) == []
    finally:
        await engine.dispose()


async def test_schedule_task_multiple_timing_args_fails_with_no_row():
    runtime, store, factory, engine, events = await _harness()
    try:
        result = await runtime.execute(
            "schedule_task",
            '{"instruction": "x", "cron": "0 9 * * *", "delay_seconds": 60}',
            _ctx(),
        )
        assert result.ok is False
        assert await _rows(factory) == []
    finally:
        await engine.dispose()


async def test_schedule_task_bad_cron_fails_with_no_row():
    runtime, store, factory, engine, events = await _harness()
    try:
        result = await runtime.execute(
            "schedule_task", '{"instruction": "x", "cron": "not a cron"}', _ctx()
        )
        assert result.ok is False
        assert await _rows(factory) == []
    finally:
        await engine.dispose()


async def test_list_my_schedules_returns_only_ctx_topic():
    runtime, store, factory, engine, events = await _harness()
    try:
        agent = FakeAgent()
        await runtime.execute(
            "schedule_task", '{"instruction": "here", "delay_seconds": 60}',
            _ctx(topic="Mine", agent=agent),
        )
        await runtime.execute(
            "schedule_task", '{"instruction": "elsewhere", "delay_seconds": 60}',
            _ctx(topic="Other", agent=agent),
        )
        result = await runtime.execute("list_my_schedules", "{}", _ctx(topic="Mine", agent=agent))
        assert result.ok
        assert "here" in result.content
        assert "elsewhere" not in result.content
    finally:
        await engine.dispose()


async def test_cancel_schedule_in_topic_succeeds():
    runtime, store, factory, engine, events = await _harness()
    try:
        await runtime.execute("schedule_task", '{"instruction": "x", "delay_seconds": 60}', _ctx())
        row = (await _rows(factory))[0]
        result = await runtime.execute(
            "cancel_schedule", f'{{"schedule_id": "{row.id}"}}', _ctx()
        )
        assert result.ok
        assert (await _rows(factory))[0].status == "cancelled"
        assert sum(e["event_type"] == "schedule_cancelled" for e in events) == 1
    finally:
        await engine.dispose()


async def test_cancel_schedule_other_topic_refused_row_untouched():
    runtime, store, factory, engine, events = await _harness()
    try:
        await runtime.execute("schedule_task", '{"instruction": "x", "delay_seconds": 60}', _ctx(topic="Mine"))
        row = (await _rows(factory))[0]
        result = await runtime.execute(
            "cancel_schedule", f'{{"schedule_id": "{row.id}"}}', _ctx(topic="Other")
        )
        assert result.ok is False
        assert (await _rows(factory))[0].status == "active"
    finally:
        await engine.dispose()


async def test_cancel_schedule_bad_uuid_fails_gracefully():
    runtime, store, factory, engine, events = await _harness()
    try:
        result = await runtime.execute("cancel_schedule", '{"schedule_id": "not-a-uuid"}', _ctx())
        assert result.ok is False
    finally:
        await engine.dispose()


def test_tools_absent_when_not_registered():
    reg = ToolRegistry()  # register_scheduling_tools never called
    assert reg.get("schedule_task") is None
    assert reg.build_schemas(TOOLS) == []
