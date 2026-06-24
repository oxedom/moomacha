"""One-off: grant the scheduling tools to the sandbox-helper agent for live e2e.

Adds schedule_task / list_my_schedules / cancel_schedule to the agent's
allowed_tools (keeping any existing tools). Idempotent.
"""

import asyncio
import uuid

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.services.crypto import SecretBox
from control_plane.schemas.agents import AgentUpdate
from control_plane.services.agent_registry import AgentRegistry

SCHEDULE_TOOLS = ["schedule_task", "list_my_schedules", "cancel_schedule"]
TARGET = "sandbox-helper"


async def main() -> None:
    settings = Settings()
    factory, _ = build_session_factory(settings.neon_database_url)
    registry = AgentRegistry(factory, SecretBox(settings.agent_fernet_key))
    agents = await registry.list()
    target = next(a for a in agents if a.name == TARGET)
    merged = list(dict.fromkeys(list(target.allowed_tools) + SCHEDULE_TOOLS))
    updated = await registry.update(
        uuid.UUID(str(target.id)), AgentUpdate(allowed_tools=merged)
    )
    print("updated allowed_tools:", updated.allowed_tools)


if __name__ == "__main__":
    asyncio.run(main())
