import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from control_plane.services.job_source import ScheduleSource
from control_plane.services.schedule_store import ClaimOutcome

logger = logging.getLogger("control_plane")


class ScheduleStoreProtocol(Protocol):
    """The subset of ScheduleStore the loop needs, so the seam is explicit."""

    async def claim_due(
        self, now: datetime, grace_seconds: int, limit: int
    ) -> list[ClaimOutcome]: ...


class EnqueueTurn(Protocol):
    """A bound enqueue_agent_turn: builds a Job + source event and enqueues it."""

    async def __call__(
        self,
        *,
        agent_id: uuid.UUID,
        channel: str,
        topic: str,
        content: str,
        source: ScheduleSource,
    ) -> None: ...


@dataclass
class SchedulerDeps:
    store: ScheduleStoreProtocol
    enqueue_turn: EnqueueTurn
    clock: Callable[[], datetime]
    grace_seconds: int
    max_due_per_tick: int


class SchedulerLoop:
    def __init__(self, deps: SchedulerDeps) -> None:
        self._deps = deps

    async def tick(self) -> None:
        now = self._deps.clock()
        outcomes = await self._deps.store.claim_due(
            now, self._deps.grace_seconds, self._deps.max_due_per_tick
        )
        for o in outcomes:
            if o.action != "fire":
                continue  # "missed" already emitted its event in claim_due
            try:
                await self._deps.enqueue_turn(
                    agent_id=o.agent_id,
                    channel=o.channel,
                    topic=o.topic,
                    content=o.instruction,
                    source=ScheduleSource(schedule_id=o.schedule_id, scheduled_for=o.scheduled_for),
                )
            except Exception:  # noqa: BLE001 - one bad fire must not skip the rest
                logger.exception("Failed to fire schedule %s", o.schedule_id)

    async def run_forever(self, interval_seconds: int) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - the loop must outlive any tick failure
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(interval_seconds)
