"""One-off: grant the Google Calendar + Tasks tools to the Claw agent.

Adds the gcal_* and gtasks_* tools to Claw's allowed_tools (keeping existing
tools). Idempotent — re-running is a no-op once granted.
"""

import asyncio
import uuid

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.schemas.agents import AgentUpdate
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox

GOOGLE_TOOLS = [
    "gcal_list_calendars",
    "gcal_list_events",
    "gcal_search_events",
    "gcal_get_event",
    "gcal_create_event",
    "gtasks_list_task_lists",
    "gtasks_list_tasks",
    "gtasks_create_task",
    "gtasks_complete_task",
    "gtasks_update_task",
    "gtasks_delete_task",
]
TARGET = "Claw"


async def main() -> None:
    settings = Settings()
    factory, _ = build_session_factory(settings.neon_database_url)
    registry = AgentRegistry(factory, SecretBox(settings.agent_fernet_key))
    agents = await registry.list()
    target = next(a for a in agents if a.name == TARGET)
    merged = list(dict.fromkeys(list(target.allowed_tools) + GOOGLE_TOOLS))
    updated = await registry.update(uuid.UUID(str(target.id)), AgentUpdate(allowed_tools=merged))
    print("updated allowed_tools:", updated.allowed_tools)


if __name__ == "__main__":
    asyncio.run(main())
