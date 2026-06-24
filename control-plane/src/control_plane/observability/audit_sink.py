from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import EventRow
from control_plane.observability.events import AgentEvent

logger = logging.getLogger("control_plane")

# attrs keys lifted into dedicated EventRow columns; the rest stay in payload.
_PROMOTED = {"agent_id", "channel", "source_message_id", "duration_ms", "status"}


def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


class AuditSink:
    """Durable, unsampled system-of-record. Maps each AgentEvent to one EventRow.

    Each emit() commits in its own short transaction so an event is never lost to a
    later turn-level rollback; the audit log is the authoritative record."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def emit(self, event: AgentEvent) -> None:
        a = event.attrs
        payload = {k: v for k, v in a.items() if k not in _PROMOTED}
        async with self._factory() as session:
            session.add(EventRow(
                actor_type="agent",
                event_type=event.type,
                payload=payload,
                actor_id=_as_uuid(a.get("agent_id")),
                related_agent_id=_as_uuid(a.get("agent_id")),
                related_channel=a.get("channel"),
                source_message_id=a.get("source_message_id"),
                trace_id=event.trace_id,
                turn_id=event.turn_id,
                seq=event.seq,
                duration_ms=a.get("duration_ms"),
                status=a.get("status"),
            ))
            await session.commit()
