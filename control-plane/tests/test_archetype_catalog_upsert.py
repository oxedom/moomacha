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


async def test_upsert_creates_when_absent(catalog):
    saved = await catalog.upsert(
        ArchetypeDefinition(name="frontlife-ops", persona="p", runtime_kind="codex")
    )
    assert saved.runtime_kind == "codex"
    assert (await catalog.get_by_name("frontlife-ops")) is not None


async def test_upsert_updates_when_present(catalog):
    await catalog.upsert(
        ArchetypeDefinition(name="a", persona="old", allowed_tools=["read_topic"])
    )
    await catalog.upsert(
        ArchetypeDefinition(
            name="a", persona="new",
            allowed_tools=["read_topic", "monday_get_board"],
            runtime_kind="codex", runtime_config={"codex": {"expose_tools": True}},
        )
    )
    got = await catalog.get_by_name("a")
    assert got.persona == "new"
    assert got.allowed_tools == ["read_topic", "monday_get_board"]
    assert got.runtime_config == {"codex": {"expose_tools": True}}
    assert len(await catalog.list_all()) == 1
