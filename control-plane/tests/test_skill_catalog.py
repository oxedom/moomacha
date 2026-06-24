# Live Neon migration (run before deploy; create_all will NOT add tables/columns to an existing DB):
#   CREATE TABLE IF NOT EXISTS knowledge_artifacts (id uuid PRIMARY KEY, name varchar(255) UNIQUE NOT NULL, body text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
#   CREATE TABLE IF NOT EXISTS skills (id uuid PRIMARY KEY, name varchar(255) UNIQUE NOT NULL, body text NOT NULL, model_era varchar(64) NOT NULL DEFAULT '', triggers jsonb NOT NULL DEFAULT '[]'::jsonb, active boolean NOT NULL DEFAULT true);
#   ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_librarian boolean NOT NULL DEFAULT false;
#   ALTER TABLE agents ADD COLUMN IF NOT EXISTS knowledge_artifact_ids jsonb NOT NULL DEFAULT '[]'::jsonb;
#   ALTER TABLE archetypes ADD COLUMN IF NOT EXISTS knowledge_artifact_ids jsonb NOT NULL DEFAULT '[]'::jsonb;

import pytest

from control_plane.db.engine import build_session_factory, create_all
from control_plane.services.skill_catalog import SkillCatalog


@pytest.fixture
async def catalog():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    yield SkillCatalog(factory)
    await engine.dispose()


async def test_upsert_and_load_by_names(catalog):
    await catalog.upsert(name="briefings", body="brief content", model_era="opus-4.x")
    loaded = await catalog.load(names=["briefings"], model_era="opus-4.x")
    assert [s.name for s in loaded] == ["briefings"]
    assert loaded[0].body == "brief content"


async def test_load_skips_mismatched_era(catalog):
    await catalog.upsert(name="legacy", body="old", model_era="gpt-4o")
    loaded = await catalog.load(names=["legacy"], model_era="opus-4.x")
    assert loaded == []


async def test_load_skips_inactive(catalog):
    await catalog.upsert(name="dep", body="x", model_era="opus-4.x", active=False)
    loaded = await catalog.load(names=["dep"], model_era="opus-4.x")
    assert loaded == []


async def test_load_empty_era_always_matches(catalog):
    await catalog.upsert(name="always", body="x", model_era="")
    loaded = await catalog.load(names=["always"], model_era="opus-4.x")
    assert [s.name for s in loaded] == ["always"]
