import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import SessionRow


def _now() -> datetime:
    return datetime.now(UTC)


class SessionStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def create(
        self, *, channel: str, topic: str, snapshot: dict,
        pool_bot_id: uuid.UUID | None, granted_caps: list[str] | None = None,
        archetype_name: str | None = None, state: str = "live",
    ) -> SessionRow:
        sid = uuid.uuid4()
        memory_ns = (
            f"agent:archetype:{archetype_name}" if archetype_name else f"agent:session:{sid}"
        )
        row = SessionRow(
            id=sid, channel=channel, topic=topic, archetype_snapshot=snapshot,
            pool_bot_id=pool_bot_id, memory_ns=memory_ns,
            granted_caps=granted_caps or [], state=state,
        )
        async with self._factory() as session:
            session.add(row)
            await session.commit()
            session.expunge_all()
            return row

    async def resolve_for_topic(self, channel: str, topic: str) -> SessionRow | None:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(SessionRow).where(
                        SessionRow.channel == channel,
                        SessionRow.topic == topic,
                        SessionRow.state != "closed",
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                session.expunge(row)
            return row

    async def _set_state(self, session_id: uuid.UUID, state: str) -> SessionRow | None:
        async with self._factory() as session:
            row = await session.get(SessionRow, session_id)
            if row is None:
                return None
            row.state = state
            row.last_active_at = _now()
            await session.commit()
            session.expunge(row)
            return row

    async def mark_dormant(self, session_id: uuid.UUID) -> SessionRow | None:
        return await self._set_state(session_id, "dormant")

    async def reopen(self, session_id: uuid.UUID) -> SessionRow | None:
        return await self._set_state(session_id, "live")

    async def close(self, session_id: uuid.UUID) -> SessionRow | None:
        return await self._set_state(session_id, "closed")

    async def mark_live(self, session_id: uuid.UUID) -> SessionRow | None:
        return await self._set_state(session_id, "live")

    async def touch(self, session_id: uuid.UUID, when: datetime | None = None) -> None:
        async with self._factory() as session:
            row = await session.get(SessionRow, session_id)
            if row is not None:
                row.last_active_at = when or _now()
                await session.commit()

    async def find_idle(self, *, now: datetime, idle_seconds: int) -> list[SessionRow]:
        async with self._factory() as session:
            rows = (
                await session.execute(select(SessionRow).where(SessionRow.state == "live"))
            ).scalars().all()
            session.expunge_all()
            return [r for r in rows if (now - r.last_active_at).total_seconds() >= idle_seconds]

    async def bind_pool_bot(self, session_id: uuid.UUID, pool_bot_id: uuid.UUID) -> None:
        async with self._factory() as session:
            row = await session.get(SessionRow, session_id)
            if row is not None:
                row.pool_bot_id = pool_bot_id
                await session.commit()
                session.expunge(row)

    async def oldest_dormant(self) -> SessionRow | None:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(SessionRow)
                    .where(SessionRow.state == "dormant")
                    .order_by(SessionRow.last_active_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is not None:
                session.expunge(row)
            return row

    async def get(self, session_id: uuid.UUID) -> SessionRow | None:
        async with self._factory() as session:
            row = await session.get(SessionRow, session_id)
            if row is not None:
                session.expunge(row)
            return row

    async def list_by_state(self, state: str) -> list[SessionRow]:
        async with self._factory() as session:
            rows = (
                await session.execute(select(SessionRow).where(SessionRow.state == state))
            ).scalars().all()
            session.expunge_all()
            return list(rows)
