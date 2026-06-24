from dataclasses import dataclass

from control_plane.schemas.agents import AgentRead
from control_plane.services.agent_registry import AgentRegistry


@dataclass
class ManagementToolContext:
    """Everything a management tool handler needs. Built per turn so
    invoking_message_text reflects the message that triggered this turn."""

    registry: AgentRegistry
    admin_client: object  # ZulipAdminClient (or a fake in tests)
    payload_url: str
    default_model: str
    invoking_message_text: str
    session_factory: object | None = None  # async_sessionmaker; for audit events
    archetypes: object | None = None  # ArchetypeCatalog | None
    pool: object | None = None  # PoolStore | None
    sessions: object | None = None  # SessionStore | None
    make_zulip_client: object | None = None  # (site, email, api_key) -> ZulipClient-like; injectable for tests


async def resolve_one(ctx: ManagementToolContext, name: str) -> tuple[AgentRead | None, str | None]:
    """Resolve a single agent by name. Returns (agent, None) on a unique match,
    or (None, error_message) when missing or ambiguous."""
    agents = await ctx.registry.list()
    exact = [a for a in agents if a.name.lower() == name.lower()]
    if len(exact) == 1:
        return exact[0], None
    partial = [a for a in agents if name.lower() in a.name.lower()]
    if len(partial) == 1:
        return partial[0], None
    if not partial:
        return None, f"No agent named '{name}'."
    names = ", ".join(a.name for a in partial)
    return None, f"Multiple agents match '{name}': {names}. Be more specific."
