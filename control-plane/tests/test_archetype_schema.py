import pytest
from pydantic import ValidationError

from control_plane.schemas.archetype import (
    ArchetypeDefinition,
    McpServerDef,
    snapshot_to_definition,
)


def test_definition_round_trips_through_snapshot():
    defn = ArchetypeDefinition(
        name="Researcher",
        persona="You are a careful research assistant.",
        allowed_tools=["tavily_search", "read_topic"],
        mcp_servers=[McpServerDef(name="ctx", transport="sse", url="https://x/sse")],
    )
    snap = defn.model_dump()
    assert snapshot_to_definition(snap) == defn


def test_defaults_are_deepagents_and_gpt4o():
    defn = ArchetypeDefinition(name="X", persona="p")
    assert defn.model_id == "gpt-4o"
    assert defn.runtime_kind == "deepagents"
    assert defn.context_message_count == 20
    assert defn.allowed_tools == []
    assert defn.mcp_servers == []
    assert defn.runtime_config == {}


def test_mcp_transport_rejects_non_sse():
    with pytest.raises(ValidationError):
        McpServerDef(name="ctx", transport="websocket", url="https://x")


def test_snapshot_to_definition_rejects_corrupt_snapshot():
    with pytest.raises(ValidationError):
        snapshot_to_definition({"persona": "missing required name field"})
