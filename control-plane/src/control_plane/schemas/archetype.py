"""Archetype definition schema — pure Pydantic, no DB dependencies.

Standalone for now; when slice B's schemas/agent_definition.py lands, reconcile
by composing AgentDefinition's shared fields rather than duplicating (tracked
follow-up, non-blocking).
"""
from typing import Literal

from pydantic import BaseModel, Field


class McpServerDef(BaseModel):
    name: str
    transport: Literal["sse"]  # only working MCP transport today
    url: str
    auth_ref: str | None = None  # names a secret; never the secret value
    tool_prefix: str | None = None


class ArchetypeDefinition(BaseModel):
    """The reusable, non-secret shape of an agent. Snapshotted into a Session."""

    # NOTE: no readable_channels — a session's channel comes from its bound topic, not the archetype (see module docstring).

    name: str
    persona: str
    model_id: str = "gpt-4o"
    context_message_count: int = 20
    allowed_tools: list[str] = Field(default_factory=list)
    knowledge_artifact_ids: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerDef] = Field(default_factory=list)
    runtime_kind: str = "deepagents"  # cattle sessions default to the DeepAgents runner (spec §2); differs from AgentRow's legacy "openai_tool_loop" default by design
    runtime_config: dict = Field(default_factory=dict)


def snapshot_to_definition(snapshot: dict) -> ArchetypeDefinition:
    """Rebuild a definition from a Session's frozen archetype_snapshot JSON."""
    return ArchetypeDefinition.model_validate(snapshot)
