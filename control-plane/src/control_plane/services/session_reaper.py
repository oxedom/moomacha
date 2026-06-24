import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from control_plane.services.session_store import SessionStore

logger = logging.getLogger("control_plane")


@dataclass
class SessionReaperDeps:
    store: SessionStore
    clock: Callable[[], datetime]
    idle_seconds: int


class SessionReaperLoop:
    def __init__(self, deps: SessionReaperDeps) -> None:
        self._deps = deps

    async def tick(self) -> None:
        now = self._deps.clock()
        idle = await self._deps.store.find_idle(now=now, idle_seconds=self._deps.idle_seconds)
        for s in idle:
            try:
                await self._deps.store.mark_dormant(s.id)
            except Exception:  # noqa: BLE001 - one bad session must not skip the rest
                logger.exception("Failed to mark session %s dormant", s.id)

    async def run_forever(self, interval_seconds: int) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - the loop must outlive any tick failure
                logger.exception("Session reaper tick failed")
            await asyncio.sleep(interval_seconds)
