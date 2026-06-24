import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.schemas.archetype import ArchetypeDefinition
from control_plane.services.archetype_catalog import ArchetypeCatalog


@pytest.fixture
async def catalog():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield ArchetypeCatalog(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def test_create_get_and_list(catalog):
    await catalog.create(ArchetypeDefinition(name="Researcher", persona="p", allowed_tools=["tavily_search"]))
    got = await catalog.get_by_name("Researcher")
    assert got is not None and got.allowed_tools == ["tavily_search"]
    assert [d.name for d in await catalog.list_all()] == ["Researcher"]


async def test_get_missing_returns_none(catalog):
    assert await catalog.get_by_name("nope") is None


async def test_search_matches_name_substring_case_insensitive(catalog):
    await catalog.create(ArchetypeDefinition(name="Web Researcher", persona="p"))
    await catalog.create(ArchetypeDefinition(name="Coder", persona="p"))
    assert [d.name for d in await catalog.search("research")] == ["Web Researcher"]
