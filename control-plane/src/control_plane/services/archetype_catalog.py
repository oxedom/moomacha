from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import ArchetypeRow
from control_plane.schemas.archetype import ArchetypeDefinition


def _to_definition(row: ArchetypeRow) -> ArchetypeDefinition:
    return ArchetypeDefinition(
        name=row.name,
        persona=row.persona,
        model_id=row.model_id,
        context_message_count=row.context_message_count,
        allowed_tools=list(row.allowed_tools or []),
        knowledge_artifact_ids=list(row.knowledge_artifact_ids or []),
        mcp_servers=list(row.mcp_servers or []),
        runtime_kind=row.runtime_kind,
        runtime_config=dict(row.runtime_config or {}),
    )


class ArchetypeCatalog:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def create(self, defn: ArchetypeDefinition) -> ArchetypeDefinition:
        row = ArchetypeRow(
            name=defn.name,
            persona=defn.persona,
            model_id=defn.model_id,
            context_message_count=defn.context_message_count,
            allowed_tools=defn.allowed_tools,
            knowledge_artifact_ids=defn.knowledge_artifact_ids,
            mcp_servers=[m.model_dump() for m in defn.mcp_servers],
            runtime_kind=defn.runtime_kind,
            runtime_config=defn.runtime_config,
        )
        async with self._factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_definition(row)

    async def upsert(self, defn: ArchetypeDefinition) -> ArchetypeDefinition:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(ArchetypeRow).where(ArchetypeRow.name == defn.name)
                )
            ).scalar_one_or_none()
            if row is None:
                row = ArchetypeRow(name=defn.name)
                session.add(row)
            row.persona = defn.persona
            row.model_id = defn.model_id
            row.context_message_count = defn.context_message_count
            row.allowed_tools = defn.allowed_tools
            row.knowledge_artifact_ids = defn.knowledge_artifact_ids
            row.mcp_servers = [m.model_dump() for m in defn.mcp_servers]
            row.runtime_kind = defn.runtime_kind
            row.runtime_config = defn.runtime_config
            await session.commit()
            await session.refresh(row)
            return _to_definition(row)

    async def get_by_name(self, name: str) -> ArchetypeDefinition | None:
        async with self._factory() as session:
            row = (
                await session.execute(select(ArchetypeRow).where(ArchetypeRow.name == name))
            ).scalar_one_or_none()
            return _to_definition(row) if row else None

    async def search(self, query: str) -> list[ArchetypeDefinition]:
        q = query.lower()
        async with self._factory() as session:
            rows = (
                await session.execute(select(ArchetypeRow).order_by(ArchetypeRow.created_at))
            ).scalars().all()
            return [_to_definition(r) for r in rows if q in r.name.lower()]

    async def list_all(self) -> list[ArchetypeDefinition]:
        async with self._factory() as session:
            rows = (
                await session.execute(select(ArchetypeRow).order_by(ArchetypeRow.created_at))
            ).scalars().all()
            return [_to_definition(r) for r in rows]
