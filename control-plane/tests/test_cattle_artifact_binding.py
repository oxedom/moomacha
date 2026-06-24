import uuid

from control_plane.services.pool_resolver import agent_from_snapshot
from control_plane.schemas.archetype import ArchetypeDefinition


class _Creds:
    bot_email = "pool-1@example.zulipchat.com"
    api_key = "k"
    outgoing_token = "t"


def test_agent_from_snapshot_carries_knowledge_artifact_ids():
    aid = str(uuid.uuid4())
    defn = ArchetypeDefinition(
        name="researcher", persona="p", allowed_tools=["read_artifact"],
        knowledge_artifact_ids=[aid],
    )
    snapshot = defn.model_dump()
    agent = agent_from_snapshot(snapshot, _Creds(), uuid.uuid4(), 123, "sandbox")
    assert agent.knowledge_artifact_ids == [aid]


def test_archetype_definition_defaults_empty_artifact_ids():
    defn = ArchetypeDefinition(name="x", persona="p")
    assert defn.knowledge_artifact_ids == []
