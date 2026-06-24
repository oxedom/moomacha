import pytest

from control_plane.db.engine import build_session_factory, create_all
from control_plane.services.knowledge_artifact_store import KnowledgeArtifactStore


@pytest.fixture
async def store():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    yield KnowledgeArtifactStore(factory)
    await engine.dispose()


async def test_upsert_then_get_by_name(store):
    await store.upsert(name="onboarding", body="# Onboarding\nStep 1...")
    art = await store.get_by_name("onboarding")
    assert art is not None
    assert art.name == "onboarding"
    assert art.body.startswith("# Onboarding")


async def test_upsert_edits_in_place(store):
    await store.upsert(name="onboarding", body="v1")
    await store.upsert(name="onboarding", body="v2")
    art = await store.get_by_name("onboarding")
    assert art.body == "v2"  # mutable, no history


async def test_list_by_ids_returns_names_and_descriptions(store):
    a = await store.upsert(name="onboarding", body="# Onboarding\nfirst line")
    await store.upsert(name="other", body="# Other\nx")
    listed = await store.list_by_ids([a.id])
    assert [x.name for x in listed] == ["onboarding"]
