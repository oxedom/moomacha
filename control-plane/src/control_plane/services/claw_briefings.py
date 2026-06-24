"""Seed Claw's recurring daily briefings through the existing scheduler path.

A thin, idempotent helper over ``ScheduleStore.create_recurring`` — the same path
the (future) scheduling tools use — so a fired briefing enters the queue exactly
like a mention-driven turn and posts as Claw in its channel/topic.

Example cadence with a configurable rest day. Cron day-of-week (croniter):
0/7=Sun, 1=Mon … 6=Sat. As shipped, the dailies skip Saturday; the evening summary
also skips Friday; the weekly review is the Saturday exception that still runs.
Adjust the cron expressions and ``TIMEZONE`` to match your own schedule.
All cron fields are interpreted in ``TIMEZONE``.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from control_plane.db.tables import ScheduledJobRow
from control_plane.services.schedule_store import ScheduleStore

TIMEZONE = "UTC"


@dataclass(frozen=True)
class Briefing:
    topic: str
    cron: str
    instruction: str


BRIEFINGS: list[Briefing] = [
    Briefing(
        topic="Claw · morning briefing",
        cron="0 9 * * 0-5",  # Sun–Fri 9am (skip rest day)
        instruction=(
            "It's 9am — morning briefing. Greet briefly, then give the top 2-3 "
            "priorities for today. Google Tasks/Calendar isn't wired into this system yet, so if "
            "you don't already have today's priorities from this topic, ask the user for them "
            "concisely rather than inventing any. Minimal emojis, no filler."
        ),
    ),
    Briefing(
        topic="Claw · afternoon check-in",
        cron="0 14 * * 0-5",  # Sun–Fri 2pm (skip rest day)
        instruction=(
            "It's 2pm — afternoon check-in. Ask the user how the day's top priorities are "
            "progressing and remind them what's left for the rest of the day. Keep it short; "
            "don't fabricate task data."
        ),
    ),
    Briefing(
        topic="Claw · evening summary",
        cron="0 20 * * 0-4",  # Sun–Thu 8pm (skip Friday eve + rest day)
        instruction=(
            "It's 8pm — evening summary. Ask the user what got done today and what carries "
            "over to tomorrow. Brief, direct, minimal emojis."
        ),
    ),
    Briefing(
        topic="Claw · weekly review",
        cron="0 19 * * 6",  # Saturday 7pm (the rest-day exception that still runs)
        instruction=(
            "It's Saturday 7pm — weekly review. Prompt the user to reflect on the week's "
            "progress and prune stale priorities. Brief and direct."
        ),
    ),
]


async def seed_claw_briefings(
    store: ScheduleStore,
    *,
    agent_id: uuid.UUID,
    channel: str,
    now: datetime,
) -> list[ScheduledJobRow]:
    """Idempotently create Claw's recurring briefing schedules.

    Skips any briefing whose ``(channel, topic)`` already has an active recurring
    row for this agent, so re-running never duplicates. Returns the rows created
    this call (empty when everything already exists).
    """
    created: list[ScheduledJobRow] = []
    for b in BRIEFINGS:
        existing = await store.list_for_topic(channel, b.topic)
        if any(r.agent_id == agent_id and r.kind == "recurring" for r in existing):
            continue
        row = await store.create_recurring(
            agent_id=agent_id,
            channel=channel,
            topic=b.topic,
            instruction=b.instruction,
            cron=b.cron,
            timezone=TIMEZONE,
            now=now,
        )
        created.append(row)
    return created
