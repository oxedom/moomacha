from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import SkillRow


class SkillCatalog:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def upsert(
        self, *, name: str, body: str, model_era: str = "", active: bool = True,
        triggers: list[str] | None = None,
    ) -> SkillRow:
        async with self._factory() as session:
            row = (
                await session.execute(select(SkillRow).where(SkillRow.name == name))
            ).scalar_one_or_none()
            if row is None:
                row = SkillRow(name=name, body=body, model_era=model_era,
                               active=active, triggers=triggers or [])
                session.add(row)
            else:
                row.body, row.model_era, row.active = body, model_era, active
                row.triggers = triggers or []
            await session.commit()
            session.expunge(row)
            return row

    async def load(self, *, names: list[str], model_era: str) -> list[SkillRow]:
        """Return active skills among `names` whose model_era matches (empty era = always)."""
        if not names:
            return []
        async with self._factory() as session:
            rows = (
                await session.execute(select(SkillRow).where(SkillRow.name.in_(names)))
            ).scalars().all()
            session.expunge_all()
        return [
            r for r in rows
            if r.active and (not r.model_era or r.model_era == model_era)
        ]
