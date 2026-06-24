"""Grant the create_interactive_response tool to the default agents (Claw + bastion).

Adds the tool to each agent's allowed_tools (keeping existing tools). Idempotent.
Run against the live DB: `cd control-plane && uv run python scripts/grant_interactive_response.py`.
"""

import asyncio
import uuid

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.schemas.agents import AgentUpdate
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox

TOOL = "create_interactive_response"
TARGETS = ["Claw", "Bastion"]


async def main() -> None:
    settings = Settings()
    factory, _ = build_session_factory(settings.neon_database_url)
    registry = AgentRegistry(factory, SecretBox(settings.agent_fernet_key))
    agents = await registry.list()
    for agent in agents:
        if agent.name not in TARGETS:
            continue
        merged = list(dict.fromkeys(list(agent.allowed_tools) + [TOOL]))
        updated = await registry.update(uuid.UUID(str(agent.id)), AgentUpdate(allowed_tools=merged))
        print(f"{agent.name}: allowed_tools -> {updated.allowed_tools}")


if __name__ == "__main__":
    asyncio.run(main())
