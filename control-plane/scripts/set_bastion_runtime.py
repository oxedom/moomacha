"""One-off: switch the Bastion agent to the DeepAgents runtime (keeping can_exec).

The deepagents runner bridges the same tool registry through ToolRuntime, so the
bastion's management tools and gated run_command exec keep working (tool_bridge
reuses build_schemas' allowed+bastion+exec selection). runtime_config is left
empty — subagents/skills are optional for a management agent.
"""

import asyncio

from sqlalchemy import select

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.db.tables import AgentRow
from control_plane.schemas.agents import AgentUpdate
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox


async def main() -> None:
    s = Settings()
    factory, _ = build_session_factory(s.neon_database_url)
    registry = AgentRegistry(factory, SecretBox(s.agent_fernet_key))
    async with factory() as session:
        row = (await session.execute(select(AgentRow).where(AgentRow.is_bastion.is_(True)))).scalar_one()
        bastion_id = row.id
        print(f"before: name={row.name} runtime_kind={row.runtime_kind} can_exec={row.can_exec}")
    updated = await registry.update(
        bastion_id,
        AgentUpdate(runtime_kind="deepagents", runtime_config={"deepagents": {}}),
    )
    print(f"after:  name={updated.name} runtime_kind={updated.runtime_kind} can_exec={updated.can_exec} cfg={updated.runtime_config}")


if __name__ == "__main__":
    asyncio.run(main())
