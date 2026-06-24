import pytest

from control_plane.db.engine import build_session_factory, create_all
from control_plane.runtime.runners.deepagents_runner import resolve_db_skills
from control_plane.services.skill_catalog import SkillCatalog


@pytest.fixture
async def catalog():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    cat = SkillCatalog(factory)
    await cat.upsert(name="briefings", body="brief body", model_era="opus-4.x")
    await cat.upsert(name="legacy", body="legacy body", model_era="gpt-4o")
    yield cat
    await engine.dispose()


async def test_resolve_db_skills_filters_by_era(catalog):
    files = await resolve_db_skills(
        catalog, names=["briefings", "legacy"], model_id="claude-opus-4-7"
    )
    assert set(files.keys()) == {"/skills/briefings/SKILL.md"}
    entry = files["/skills/briefings/SKILL.md"]
    # create_file_data wraps the body; the content must be retrievable
    assert "brief body" in str(entry)


async def test_resolve_db_skills_empty_when_no_names(catalog):
    assert await resolve_db_skills(catalog, names=[], model_id="claude-opus-4-7") == {}


async def test_resolve_db_skills_none_catalog():
    assert await resolve_db_skills(None, names=["briefings"], model_id="claude-opus-4-7") == {}


async def test_resolve_db_skills_normalizes_path_strings(catalog):
    # Skills are configured in personas as path strings like "/skills/briefings/"
    # resolve_db_skills must strip the path format before querying by name
    files = await resolve_db_skills(
        catalog, names=["/skills/briefings/", "/skills/legacy/"], model_id="claude-opus-4-7"
    )
    assert set(files.keys()) == {"/skills/briefings/SKILL.md"}
