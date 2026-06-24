"""Idempotent create-or-update of a persona-JSON agent against the registry.

Source of truth is a persona JSON (the persona-JSON shape: name + identity/voice/
preferences/memory/artifacts + resources.{model,channels,tools,bot,runtime}).
The agent is matched by name: created if absent, otherwise registry.update() is
applied. Because this connects to whatever NEON_DATABASE_URL points at, running
it locally with a prod DB URL updates prod directly.

Secrets are read from the environment only (never the JSON):
  AGENT_API_KEY, AGENT_OUTGOING_TOKEN  — required only for CREATE.
On UPDATE they are left untouched (existing stored creds are preserved).

    uv run --no-sync python scripts/sync_persona.py personas/<your-persona>.json
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.personas.claw_persona import render_persona
from control_plane.schemas.agents import AgentCreate, AgentUpdate
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: sync_persona.py <persona.json>")
    obj = json.loads(Path(sys.argv[1]).resolve().read_text())
    res = obj["resources"]
    persona = render_persona(obj)

    settings = Settings()
    session_factory, _engine = build_session_factory(settings.neon_database_url)
    registry = AgentRegistry(session_factory, SecretBox(settings.agent_fernet_key))

    agents = await registry.list()
    existing = next((a for a in agents if a.name == obj["name"]), None)

    if existing is None:
        bot = res.get("bot", {})
        api_key = os.environ.get("AGENT_API_KEY")
        outgoing_token = os.environ.get("AGENT_OUTGOING_TOKEN")
        if not api_key or not outgoing_token:
            raise SystemExit(
                f"{obj['name']!r} does not exist yet; CREATE needs AGENT_API_KEY / "
                "AGENT_OUTGOING_TOKEN in the environment."
            )
        if not bot.get("email") or not bot.get("bot_id"):
            raise SystemExit("resources.bot.email/bot_id required to create the agent.")
        created = await registry.create(
            AgentCreate(
                name=obj["name"],
                persona=persona,
                model_id=res["model"],
                readable_channels=res["channels"],
                allowed_tools=res["tools"],
                zulip_bot_id=bot["bot_id"],
                zulip_bot_email=bot["email"],
                zulip_api_key=api_key,
                zulip_outgoing_token=outgoing_token,
                runtime_kind=res["runtime"]["runtime_kind"],
                runtime_config=res["runtime"]["runtime_config"],
            )
        )
        print(f"CREATED {created.name} id={created.id} tools={len(created.allowed_tools)}")
        return

    print("REVERT-INFO (save this to undo):")
    print(f"  name={existing.name} id={existing.id}")
    print(f"  prior allowed_tools={json.dumps(existing.allowed_tools)}")
    print(f"  prior channels={json.dumps(existing.readable_channels)}")
    print(f"  prior model_id={existing.model_id!r}")

    updated = await registry.update(
        existing.id,
        AgentUpdate(
            persona=persona,
            model_id=res["model"],
            readable_channels=res["channels"],
            allowed_tools=res["tools"],
            runtime_kind=res["runtime"]["runtime_kind"],
            runtime_config=res["runtime"]["runtime_config"],
        ),
    )
    print("\nUPDATED:")
    print(f"  allowed_tools={json.dumps(updated.allowed_tools)}")
    print(f"  channels={json.dumps(updated.readable_channels)}")
    print(f"  model_id={updated.model_id!r}")


if __name__ == "__main__":
    asyncio.run(main())
