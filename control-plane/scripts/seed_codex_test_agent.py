"""Repurpose sandbox-helper into a codex tool-bridge test agent: runtime_kind=codex,
expose_tools=true, minimal toolset (read_topic + one Google tool), danger-full-access,
codex model. Prints the PRIOR values so the change is revertible.

    uv run --no-sync python scripts/seed_codex_test_agent.py
"""
import asyncio
import json

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.schemas.agents import AgentUpdate
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox

TEST_AGENT_NAME = "sandbox-helper"
NEW_TOOLS = ["read_topic", "gtasks_list_task_lists"]
NEW_RUNTIME_CONFIG = {
    "codex": {
        "sandbox_mode": "danger-full-access",
        "model": "gpt-5.1-codex",
        "expose_tools": True,
    }
}


async def main() -> None:
    settings = Settings()
    session_factory, _engine = build_session_factory(settings.neon_database_url)
    registry = AgentRegistry(session_factory, SecretBox(settings.agent_fernet_key))

    agents = await registry.list()
    agent = next((a for a in agents if a.name == TEST_AGENT_NAME), None)
    if agent is None:
        raise SystemExit(
            f"No agent named {TEST_AGENT_NAME!r}. Available: {[a.name for a in agents]}"
        )

    print("REVERT-INFO (save this to undo):")
    print(f"  name={agent.name} id={agent.id}")
    print(f"  prior runtime_kind={agent.runtime_kind!r}")
    print(f"  prior runtime_config={json.dumps(agent.runtime_config)}")
    print(f"  prior allowed_tools={json.dumps(agent.allowed_tools)}")
    print(f"  prior channels={json.dumps(agent.readable_channels)}")

    updated = await registry.update(
        agent.id,
        AgentUpdate(
            runtime_kind="codex",
            runtime_config=NEW_RUNTIME_CONFIG,
            allowed_tools=NEW_TOOLS,
            readable_channels=["sandbox"],
        ),
    )
    print("\nAFTER:")
    print(f"  runtime_kind={updated.runtime_kind!r}")
    print(f"  runtime_config={json.dumps(updated.runtime_config)}")
    print(f"  allowed_tools={json.dumps(updated.allowed_tools)}")
    print(f"  channels={json.dumps(updated.readable_channels)}")


if __name__ == "__main__":
    asyncio.run(main())
