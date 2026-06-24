import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import KnowledgeArtifactRow


class KnowledgeArtifactStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def upsert(self, *, name: str, body: str) -> KnowledgeArtifactRow:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(KnowledgeArtifactRow).where(KnowledgeArtifactRow.name == name)
                )
            ).scalar_one_or_none()
            if row is None:
                row = KnowledgeArtifactRow(name=name, body=body)
                session.add(row)
            else:
                row.body = body
            await session.commit()
            session.expunge(row)
            return row

    async def get_by_name(self, name: str) -> KnowledgeArtifactRow | None:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(KnowledgeArtifactRow).where(KnowledgeArtifactRow.name == name)
                )
            ).scalar_one_or_none()
            if row is not None:
                session.expunge(row)
            return row

    async def list_by_ids(self, ids: list[uuid.UUID]) -> list[KnowledgeArtifactRow]:
        if not ids:
            return []
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(KnowledgeArtifactRow)
                    .where(KnowledgeArtifactRow.id.in_(ids))
                    .order_by(KnowledgeArtifactRow.name)
                )
            ).scalars().all()
            session.expunge_all()
            return list(rows)
