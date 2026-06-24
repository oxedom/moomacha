import uuid

import pytest
from cryptography.fernet import Fernet

from control_plane.db.engine import build_session_factory, create_all
from control_plane.schemas.agents import AgentCreate, AgentRead
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox
from control_plane.services.exceptions import AgentAlreadyExistsError


@pytest.fixture
async def registry():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    box = SecretBox(Fernet.generate_key().decode())
    yield AgentRegistry(session_factory=factory, secret_box=box)
    await engine.dispose()


def _create(**overrides) -> AgentCreate:
    base = dict(
        name="researcher",
        persona="You research things.",
        zulip_bot_id=42,
        zulip_bot_email="researcher-bot@example.zulipchat.com",
        zulip_api_key="bot-secret",
        zulip_outgoing_token="tok",
        readable_channels=["sandbox"],
    )
    base.update(overrides)
    return AgentCreate(**base)


async def test_create_returns_read_without_secrets(registry):
    read = await registry.create(_create())

    assert isinstance(read, AgentRead)
    assert read.name == "researcher"
    assert read.model_id == "gpt-4o"
    assert not hasattr(read, "zulip_api_key")
    assert not hasattr(read, "zulip_outgoing_token")


async def test_resolve_by_bot_email_decrypts_key(registry):
    await registry.create(_create())

    resolved = await registry.resolve_by_bot_email("researcher-bot@example.zulipchat.com")

    assert resolved is not None
    assert resolved.zulip_api_key == "bot-secret"  # decrypted
    assert resolved.zulip_outgoing_token == "tok"


async def test_resolve_unknown_returns_none(registry):
    assert await registry.resolve_by_bot_email("nobody@x") is None


async def test_create_duplicate_raises(registry):
    await registry.create(_create())
    with pytest.raises(AgentAlreadyExistsError):
        await registry.create(_create())


async def test_get_returns_read(registry):
    created = await registry.create(_create())
    fetched = await registry.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert not hasattr(fetched, "zulip_api_key")


async def test_get_unknown_returns_none(registry):
    assert await registry.get(uuid.uuid4()) is None


async def test_delete_removes_agent(registry):
    created = await registry.create(_create())
    assert await registry.delete(created.id) is True
    assert await registry.get(created.id) is None


async def test_delete_unknown_returns_false(registry):
    assert await registry.delete(uuid.uuid4()) is False


async def test_create_persists_allowed_tools(registry):
    created = await registry.create(_create(allowed_tools=["read_topic", "read_channel"]))
    assert created.allowed_tools == ["read_topic", "read_channel"]


async def test_allowed_tools_defaults_to_empty(registry):
    created = await registry.create(_create())
    assert created.allowed_tools == []


async def test_resolve_returns_allowed_tools(registry):
    await registry.create(_create(allowed_tools=["read_topic"]))
    resolved = await registry.resolve_by_bot_email("researcher-bot@example.zulipchat.com")
    assert resolved is not None
    assert resolved.allowed_tools == ["read_topic"]


async def test_is_librarian_defaults_false_and_roundtrips(registry):
    await registry.create(_create(name="lib-default", zulip_bot_email="lib-default@example.zulipchat.com"))
    resolved = await registry.resolve_by_bot_email("lib-default@example.zulipchat.com")
    assert resolved is not None
    assert resolved.is_librarian is False


async def test_is_librarian_true_roundtrips_through_resolve(registry):
    await registry.create(
        _create(name="the-librarian", zulip_bot_email="the-librarian@example.zulipchat.com", is_librarian=True)
    )
    resolved = await registry.resolve_by_bot_email("the-librarian@example.zulipchat.com")
    assert resolved is not None
    assert resolved.is_librarian is True


async def test_knowledge_artifact_ids_default_empty_and_roundtrip(registry):
    import uuid
    aid = str(uuid.uuid4())
    await registry.create(
        _create(name="kn-agent", zulip_bot_email="kn-agent@example.zulipchat.com",
                knowledge_artifact_ids=[aid])
    )
    resolved = await registry.resolve_by_bot_email("kn-agent@example.zulipchat.com")
    assert resolved is not None
    assert resolved.knowledge_artifact_ids == [aid]


async def test_knowledge_artifact_ids_defaults_to_empty_list(registry):
    await registry.create(_create(name="kn-empty", zulip_bot_email="kn-empty@example.zulipchat.com"))
    resolved = await registry.resolve_by_bot_email("kn-empty@example.zulipchat.com")
    assert resolved is not None
    assert resolved.knowledge_artifact_ids == []
