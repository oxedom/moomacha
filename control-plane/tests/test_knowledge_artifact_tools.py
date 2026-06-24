from types import SimpleNamespace

import pytest

from control_plane.db.engine import build_session_factory, create_all
from control_plane.runtime.tools.knowledge_artifacts import register_knowledge_artifact_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime
from control_plane.services.knowledge_artifact_store import KnowledgeArtifactStore


@pytest.fixture
async def env():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    store = KnowledgeArtifactStore(factory)
    reg = ToolRegistry()
    register_knowledge_artifact_tools(reg, store)
    yield store, ToolRuntime(reg)
    await engine.dispose()


def _ctx(artifact_ids):
    agent = SimpleNamespace(
        id="a1",
        allowed_tools=["list_artifacts", "read_artifact"],
        knowledge_artifact_ids=artifact_ids,
    )
    return ToolContext(agent=agent, zulip=None, channel="c", topic="t")


async def test_list_artifacts_scoped_to_bound_ids(env):
    store, runtime = env
    bound = await store.upsert(name="onboarding", body="# Onboarding\nfirst line of body")
    await store.upsert(name="secret", body="# Secret\nnope")
    res = await runtime.execute("list_artifacts", "{}", _ctx([str(bound.id)]))
    assert res.ok
    assert "onboarding" in res.content
    assert "secret" not in res.content


async def test_read_artifact_returns_body_when_bound(env):
    store, runtime = env
    bound = await store.upsert(name="onboarding", body="# Onboarding\nthe body")
    res = await runtime.execute(
        "read_artifact", '{"name": "onboarding"}', _ctx([str(bound.id)])
    )
    assert res.ok and "the body" in res.content


async def test_read_artifact_refused_when_not_bound(env):
    store, runtime = env
    await store.upsert(name="onboarding", body="x")
    res = await runtime.execute("read_artifact", '{"name": "onboarding"}', _ctx([]))
    assert res.ok is False
    assert "not available" in res.content.lower()
