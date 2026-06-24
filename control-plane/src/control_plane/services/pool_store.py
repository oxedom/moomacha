import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import PoolBotRow
from control_plane.services.crypto import SecretBox


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class PoolBotCreds:
    bot_email: str
    api_key: str
    outgoing_token: str


class PoolStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], secret_box: SecretBox) -> None:
        self._factory = session_factory
        self._box = secret_box

    async def seed(self, *, zulip_bot_id: int, zulip_bot_email: str, api_key: str, outgoing_token: str) -> PoolBotRow:
        row = PoolBotRow(
            zulip_bot_id=zulip_bot_id, zulip_bot_email=zulip_bot_email,
            zulip_api_key_encrypted=self._box.encrypt(api_key),
            zulip_outgoing_token_encrypted=self._box.encrypt(outgoing_token),
            status="free",
        )
        async with self._factory() as session:
            session.add(row)
            await session.commit()
            session.expunge(row)
            return row

    async def lease(self, *, session_id: uuid.UUID, display_name: str) -> PoolBotRow | None:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(PoolBotRow).where(PoolBotRow.status == "free")
                    .order_by(PoolBotRow.last_active_at).limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = "leased"
            row.current_name = display_name
            row.current_session_id = session_id
            row.last_active_at = _now()
            await session.commit()
            session.expunge(row)
            return row

    async def release(self, pool_bot_id: uuid.UUID) -> None:
        async with self._factory() as session:
            row = await session.get(PoolBotRow, pool_bot_id)
            if row is None:
                return
            row.status = "free"
            row.current_name = None
            row.current_session_id = None
            row.last_active_at = _now()
            await session.commit()

    async def get(self, pool_bot_id: uuid.UUID) -> PoolBotRow | None:
        async with self._factory() as session:
            row = await session.get(PoolBotRow, pool_bot_id)
            if row is not None:
                session.expunge(row)
            return row

    async def list_all(self) -> list[PoolBotRow]:
        async with self._factory() as session:
            rows = (await session.execute(select(PoolBotRow))).scalars().all()
            session.expunge_all()
            return list(rows)

    async def count_free(self) -> int:
        async with self._factory() as session:
            return (
                await session.execute(
                    select(func.count()).select_from(PoolBotRow).where(PoolBotRow.status == "free")
                )
            ).scalar_one()

    async def find_leased(self) -> list[PoolBotRow]:
        async with self._factory() as session:
            rows = (
                await session.execute(select(PoolBotRow).where(PoolBotRow.status == "leased"))
            ).scalars().all()
            session.expunge_all()
            return list(rows)

    async def release_for_session(self, session_id: uuid.UUID) -> None:
        """Free any bot(s) whose current_session_id points at session_id. Idempotent."""
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(PoolBotRow).where(PoolBotRow.current_session_id == session_id)
                )
            ).scalars().all()
            for row in rows:
                row.status = "free"
                row.current_name = None
                row.current_session_id = None
                row.last_active_at = _now()
            await session.commit()

    async def find_by_email(self, email: str) -> PoolBotRow | None:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(PoolBotRow).where(PoolBotRow.zulip_bot_email == email)
                )
            ).scalar_one_or_none()
            if row is not None:
                session.expunge(row)
            return row

    async def resolve_creds(self, pool_bot_id: uuid.UUID) -> PoolBotCreds | None:
        async with self._factory() as session:
            row = await session.get(PoolBotRow, pool_bot_id)
            if row is None:
                return None
            return PoolBotCreds(
                bot_email=row.zulip_bot_email,
                api_key=self._box.decrypt(row.zulip_api_key_encrypted),
                outgoing_token=self._box.decrypt(row.zulip_outgoing_token_encrypted),
            )
