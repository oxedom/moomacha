"""One-off: grant image generation tools to selected agents.

Defaults to Claw. Idempotent — re-running is a no-op once granted.
"""

import asyncio
import uuid

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.schemas.agents import AgentUpdate
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox

IMAGE_TOOLS = ["generate_image"]
TARGETS = ["Claw"]


async def main() -> None:
    settings = Settings()
    factory, _ = build_session_factory(settings.neon_database_url)
    registry = AgentRegistry(factory, SecretBox(settings.agent_fernet_key))
    agents = await registry.list()
    by_name = {agent.name: agent for agent in agents}
    for target_name in TARGETS:
        target = by_name.get(target_name)
        if target is None:
            print(f"skip missing agent: {target_name}")
            continue
        merged = list(dict.fromkeys(list(target.allowed_tools) + IMAGE_TOOLS))
        updated = await registry.update(uuid.UUID(str(target.id)), AgentUpdate(allowed_tools=merged))
        print(f"{target_name} allowed_tools:", updated.allowed_tools)


if __name__ == "__main__":
    asyncio.run(main())
