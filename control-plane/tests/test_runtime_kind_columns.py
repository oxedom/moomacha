# Live Neon migration (run before deploy; create_all will NOT add columns to an existing table):
# ALTER TABLE agents
#   ADD COLUMN IF NOT EXISTS runtime_kind   text  NOT NULL DEFAULT 'openai_tool_loop',
#   ADD COLUMN IF NOT EXISTS runtime_config jsonb NOT NULL DEFAULT '{}'::jsonb;

import pytest
from cryptography.fernet import Fernet

from control_plane.db.engine import build_session_factory, create_all
from control_plane.schemas.agents import AgentCreate, AgentUpdate
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox


@pytest.fixture
async def registry():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    box = SecretBox(Fernet.generate_key().decode())
    yield AgentRegistry(session_factory=factory, secret_box=box)
    await engine.dispose()


def _create(**overrides) -> AgentCreate:
    base = dict(
        name="test-agent",
        persona="You are a test agent.",
        zulip_bot_id=99,
        zulip_bot_email="test-agent@example.zulipchat.com",
        zulip_api_key="test-api-key",
        zulip_outgoing_token="test-token",
        readable_channels=["sandbox"],
    )
    base.update(overrides)
    return AgentCreate(**base)


async def test_default_runtime_kind_on_create(registry):
    """Agent created without runtime fields defaults to openai_tool_loop."""
    read = await registry.create(_create())
    assert read.runtime_kind == "openai_tool_loop"


async def test_default_runtime_kind_on_resolve(registry):
    """Resolved agent without runtime fields defaults to openai_tool_loop and empty config."""
    await registry.create(_create())
    resolved = await registry.resolve_by_bot_email("test-agent@example.zulipchat.com")
    assert resolved is not None
    assert resolved.runtime_kind == "openai_tool_loop"
    assert resolved.runtime_config == {}


async def test_custom_runtime_kind_roundtrips_through_read(registry):
    """Agent created with deepagents runtime_kind and config round-trips through AgentRead."""
    config = {"deepagents": {"skills": ["/skills/personal-assistant/"], "subagents": ["researcher"]}}
    read = await registry.create(_create(
        name="deepagents-test",
        zulip_bot_email="deepagents-test@example.zulipchat.com",
        runtime_kind="deepagents",
        runtime_config=config,
    ))
    assert read.runtime_kind == "deepagents"
    assert read.runtime_config == config


async def test_custom_runtime_config_roundtrips_through_resolve(registry):
    """Agent created with deepagents config round-trips through resolve_by_bot_email."""
    config = {"deepagents": {"skills": ["/skills/personal-assistant/"], "subagents": ["researcher"]}}
    await registry.create(_create(
        name="deepagents-resolve",
        zulip_bot_email="deepagents-resolve@example.zulipchat.com",
        runtime_kind="deepagents",
        runtime_config=config,
    ))
    resolved = await registry.resolve_by_bot_email("deepagents-resolve@example.zulipchat.com")
    assert resolved is not None
    assert resolved.runtime_kind == "deepagents"
    assert resolved.runtime_config == config


async def test_update_runtime_kind(registry):
    """AgentUpdate with runtime_kind returns updated AgentRead."""
    created = await registry.create(_create())
    assert created.runtime_kind == "openai_tool_loop"

    updated = await registry.update(created.id, AgentUpdate(runtime_kind="deepagents"))
    assert updated is not None
    assert updated.runtime_kind == "deepagents"
