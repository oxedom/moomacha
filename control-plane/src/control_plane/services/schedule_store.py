import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import ScheduledJobRow

logger = logging.getLogger("control_plane")

WriteEvent = Callable[..., Awaitable[None]]


def next_cron(expr: str, after: datetime, tz_name: str) -> datetime:
    """Next cron occurrence strictly after `after`, returned in UTC.

    Cron fields are interpreted in `tz_name` (so "0 9 * * *" means 9am local),
    then converted back to UTC for storage and comparison.
    """
    if after.tzinfo is None:
        raise ValueError("next_cron requires a timezone-aware datetime")
    tz = ZoneInfo(tz_name)
    local_after = after.astimezone(tz)
    nxt = croniter(expr, local_after).get_next(datetime)  # aware, in tz
    return nxt.astimezone(UTC)


@dataclass
class ClaimOutcome:
    """A row claim_due acted on. action is what the loop should do next."""

    schedule_id: uuid.UUID
    agent_id: uuid.UUID
    channel: str
    topic: str
    instruction: str
    scheduled_for: datetime  # the due occurrence (used for the fire_key)
    action: str  # "fire" | "missed"


class ScheduleStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        write_event: WriteEvent,
    ) -> None:
        self._factory = session_factory
        self._write_event = write_event

    async def _emit(self, event_type: str, row: ScheduledJobRow) -> None:
        await self._write_event(
            actor_type="agent",
            event_type=event_type,
            payload={"schedule_id": str(row.id), "kind": row.kind},
            actor_id=row.agent_id,
            related_agent_id=row.agent_id,
            related_channel=row.channel,
        )

    async def create_one_shot(
        self, *, agent_id: uuid.UUID, channel: str, topic: str,
        instruction: str, run_at: datetime,
    ) -> ScheduledJobRow:
        """Create and persist a one-shot scheduled job.

        Emits a schedule_created event; callers (e.g. tool adapters) must not re-emit it.
        """
        row = ScheduledJobRow(
            agent_id=agent_id, channel=channel, topic=topic, kind="one_shot",
            instruction=instruction, run_at=run_at, next_run_at=run_at, status="active",
        )
        async with self._factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        await self._emit("schedule_created", row)
        return row

    async def create_recurring(
        self, *, agent_id: uuid.UUID, channel: str, topic: str,
        instruction: str, cron: str, timezone: str, now: datetime,
    ) -> ScheduledJobRow:
        """Create and persist a recurring scheduled job.

        Emits a schedule_created event; callers (e.g. tool adapters) must not re-emit it.
        """
        row = ScheduledJobRow(
            agent_id=agent_id, channel=channel, topic=topic, kind="recurring",
            instruction=instruction, cron_expression=cron, timezone=timezone,
            next_run_at=next_cron(cron, now, timezone), status="active",
        )
        async with self._factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        await self._emit("schedule_created", row)
        return row

    async def list_for_topic(
        self, channel: str, topic: str, include_inactive: bool = False,
    ) -> list[ScheduledJobRow]:
        conditions = [
            ScheduledJobRow.channel == channel,
            ScheduledJobRow.topic == topic,
        ]
        if not include_inactive:
            conditions.append(ScheduledJobRow.status == "active")
        stmt = select(ScheduledJobRow).where(*conditions).order_by(ScheduledJobRow.next_run_at)
        async with self._factory() as session:
            rows = list((await session.execute(stmt)).scalars())
            session.expunge_all()
            return rows

    async def cancel(self, schedule_id: uuid.UUID, channel: str, topic: str) -> bool:
        """Cancel an active schedule by id, scoped to the given channel/topic.

        Emits a schedule_cancelled event on success; callers must not re-emit it.
        Returns True if the schedule was found and cancelled, False otherwise.
        """
        async with self._factory() as session:
            row = (await session.execute(
                select(ScheduledJobRow).where(ScheduledJobRow.id == schedule_id)
            )).scalar_one_or_none()
            if row is None or row.channel != channel or row.topic != topic:
                return False
            if row.status != "active":
                return False
            row.status = "cancelled"
            await session.commit()
            await session.refresh(row)
        await self._emit("schedule_cancelled", row)
        return True

    async def claim_due(
        self, now: datetime, grace_seconds: int, limit: int,
    ) -> list[ClaimOutcome]:
        outcomes: list[ClaimOutcome] = []
        missed_rows: list[ScheduledJobRow] = []
        errored_rows: list[tuple[ScheduledJobRow, str]] = []
        async with self._factory() as session:
            stmt = (
                select(ScheduledJobRow)
                .where(
                    ScheduledJobRow.status == "active",
                    ScheduledJobRow.next_run_at <= now,
                )
                .order_by(ScheduledJobRow.next_run_at)
                .limit(limit)
                .with_for_update(skip_locked=True)  # no-op on sqlite; the guard on Postgres
            )
            rows = list((await session.execute(stmt)).scalars())
            for row in rows:
                try:
                    scheduled_for = row.next_run_at
                    within_grace = (now - scheduled_for).total_seconds() <= grace_seconds
                    if row.kind == "one_shot":
                        if within_grace:
                            row.status = "completed"
                            row.last_run_at = now  # actual claim time, not the scheduled occurrence
                            outcomes.append(self._fire_outcome(row, scheduled_for))
                        else:
                            row.status = "missed"
                            missed_rows.append(row)
                            outcomes.append(self._miss_outcome(row, scheduled_for))
                    else:  # recurring
                        if not row.cron_expression:
                            raise ValueError("recurring schedule has no cron_expression")
                        # Always advance strictly past `now` so a backlog coalesces to one.
                        row.next_run_at = next_cron(row.cron_expression, now, row.timezone)
                        if within_grace:
                            row.last_run_at = now
                            outcomes.append(self._fire_outcome(row, scheduled_for))
                        else:
                            # past grace -> rolled forward silently, no outcome
                            logger.debug("recurring schedule %s rolled past grace; next=%s", row.id, row.next_run_at)
                except Exception as exc:  # noqa: BLE001 - quarantine a bad row; never wedge the batch
                    row.status = "error"
                    errored_rows.append((row, str(exc)))
            await session.commit()
        # Emitted after commit. Rows are detached but expire_on_commit=False keeps
        # their scalar attrs readable. NOTE: "schedule_fired" is intentionally NOT
        # emitted here — the caller emits it after acting on each "fire" outcome.
        for row in missed_rows:
            await self._emit("schedule_missed", row)
        for row, error in errored_rows:
            await self._write_event(
                actor_type="agent",
                event_type="schedule_errored",
                payload={"schedule_id": str(row.id), "kind": row.kind, "error": error},
                actor_id=row.agent_id,
                related_agent_id=row.agent_id,
                related_channel=row.channel,
            )
        return outcomes

    @staticmethod
    def _fire_outcome(row: ScheduledJobRow, scheduled_for: datetime) -> ClaimOutcome:
        return ScheduleStore._outcome(row, scheduled_for, "fire")

    @staticmethod
    def _miss_outcome(row: ScheduledJobRow, scheduled_for: datetime) -> ClaimOutcome:
        return ScheduleStore._outcome(row, scheduled_for, "missed")

    @staticmethod
    def _outcome(row: ScheduledJobRow, scheduled_for: datetime, action: str) -> ClaimOutcome:
        return ClaimOutcome(
            schedule_id=row.id, agent_id=row.agent_id, channel=row.channel,
            topic=row.topic, instruction=row.instruction,
            scheduled_for=scheduled_for, action=action,
        )
