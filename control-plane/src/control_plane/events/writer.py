import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import EventRow


async def write_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    actor_type: str,
    event_type: str,
    payload: dict | None = None,
    actor_id: uuid.UUID | None = None,
    related_agent_id: uuid.UUID | None = None,
    related_channel: str | None = None,
    source_message_id: int | None = None,
    trace_id: str | None = None,
) -> None:
    async with session_factory() as session:
        session.add(
            EventRow(
                actor_type=actor_type,
                event_type=event_type,
                payload=payload or {},
                actor_id=actor_id,
                related_agent_id=related_agent_id,
                related_channel=related_channel,
                source_message_id=source_message_id,
                trace_id=trace_id,
            )
        )
        await session.commit()
