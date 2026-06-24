import pytest
from sqlalchemy import select

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import AgentRow, EventRow
from control_plane.services.bastion_seeder import seed_bastion
from control_plane.services.crypto import SecretBox

TEST_FERNET_KEY = "kjsN26tcj4F3Qe7dalPMBJO2MC7sK8ZRd54LNo0mz1A="


class _Settings:
    """Minimal stand-in for control_plane.config.Settings."""

    bastion_name = "Bastion"
    bastion_bot_id = 9001
    bastion_bot_email = "bastion@x"
    bastion_api_key = "bastion-key"
    bastion_outgoing_token = "bastion-tok"
    bastion_persona = None
    bastion_model_id = None
    bastion_channels = "sandbox"
    bastion_channel_list = ["sandbox"]


@pytest.fixture
async def factory():
    f, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    yield f
    await engine.dispose()


async def test_agent_row_has_is_bastion_defaulting_false():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    try:
        async with factory() as session:
            row = AgentRow(
                name="plain",
                persona="p",
                model_id="gpt-4o",
                zulip_bot_id=1,
                zulip_bot_email="plain@x",
                zulip_api_key_encrypted="enc",
                zulip_outgoing_token_encrypted="enc",
                readable_channels=[],
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            assert row.is_bastion is False
    finally:
        await engine.dispose()


async def test_seed_skips_when_unconfigured(factory):
    s = _Settings()
    s.bastion_bot_email = None
    agent_id = await seed_bastion(factory, s, SecretBox(TEST_FERNET_KEY))
    assert agent_id is None
    async with factory() as session:
        rows = (await session.execute(select(AgentRow))).scalars().all()
    assert rows == []


async def test_seed_creates_bastion_when_absent(factory):
    agent_id = await seed_bastion(factory, _Settings(), SecretBox(TEST_FERNET_KEY))
    assert agent_id is not None
    async with factory() as session:
        rows = (await session.execute(select(AgentRow).where(AgentRow.is_bastion == True))).scalars().all()  # noqa: E712
    assert len(rows) == 1
    assert rows[0].zulip_bot_email == "bastion@x"
    assert rows[0].zulip_api_key_encrypted != "bastion-key"  # encrypted at rest
    async with factory() as session:
        events = {e.event_type for e in (await session.execute(select(EventRow))).scalars()}
    assert "agent_seeded" in events


async def test_seed_is_idempotent_and_refreshes_creds(factory):
    box = SecretBox(TEST_FERNET_KEY)
    first_id = await seed_bastion(factory, _Settings(), box)
    s2 = _Settings()
    s2.bastion_outgoing_token = "rotated-tok"
    second_id = await seed_bastion(factory, s2, box)
    assert second_id == first_id  # same row, not a duplicate
    async with factory() as session:
        rows = (await session.execute(select(AgentRow).where(AgentRow.is_bastion == True))).scalars().all()  # noqa: E712
    assert len(rows) == 1
    assert box.decrypt(rows[0].zulip_outgoing_token_encrypted) == "rotated-tok"


async def test_seed_uses_default_model_with_real_settings(factory, monkeypatch):
    from control_plane.config import Settings

    monkeypatch.setenv("ZULIP_SITE", "https://x.zulipchat.com")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("OPENAI_KEY", "sk-x")
    monkeypatch.setenv("AGENT_FERNET_KEY", TEST_FERNET_KEY)
    monkeypatch.setenv("BASTION_BOT_ID", "9001")
    monkeypatch.setenv("BASTION_BOT_EMAIL", "bastion@x")
    monkeypatch.setenv("BASTION_API_KEY", "bastion-key")
    monkeypatch.setenv("BASTION_OUTGOING_TOKEN", "bastion-tok")
    # BASTION_MODEL_ID intentionally unset -> must fall back to "gpt-4o"
    settings = Settings(_env_file=None)

    agent_id = await seed_bastion(factory, settings, SecretBox(TEST_FERNET_KEY))
    assert agent_id is not None
    async with factory() as session:
        rows = (await session.execute(select(AgentRow).where(AgentRow.is_bastion == True))).scalars().all()  # noqa: E712
    assert rows[0].model_id == "gpt-4o"


class _NoCredsSettings:
    """Settings with no manual bastion creds, triggering the auto-provision path."""

    bastion_name = "Bastion"
    bastion_bot_id = None
    bastion_bot_email = None
    bastion_api_key = None
    bastion_outgoing_token = None
    bastion_persona = None
    bastion_model_id = None
    bastion_channels = "sandbox"
    bastion_channel_list = ["sandbox"]
    public_base_url = "https://agents.example"
    zulip_site = "https://x.zulipchat.com"


class _SeederFakeAdmin:
    def __init__(self, outgoing_token="bastion-webhook-tok"):
        from control_plane.services.zulip_admin import ProvisionResult

        self._result = ProvisionResult(
            bot_id=321, api_key="auto-key", bot_email="bastion-bot@x", outgoing_token=outgoing_token
        )
        self.calls = []

    async def provision_bot(self, full_name, short_name, payload_url, channels):
        self.calls.append((full_name, short_name, payload_url, tuple(channels)))
        return self._result


async def test_seed_skips_and_warns_when_no_creds_even_with_admin(factory, caplog):
    """Auto-provision is intentionally NOT attempted: it cannot capture the outgoing
    token cleanly and produced a startup traceback. With creds absent we skip and warn
    that the (recommended) bastion needs BASTION_* set — even when an admin client exists.
    """
    import logging

    admin = _SeederFakeAdmin()
    with caplog.at_level(logging.WARNING, logger="control_plane"):
        agent_id = await seed_bastion(factory, _NoCredsSettings(), SecretBox(TEST_FERNET_KEY), admin_client=admin)

    assert agent_id is None
    assert admin.calls == []  # provisioning never attempted
    async with factory() as session:
        rows = (await session.execute(select(AgentRow))).scalars().all()
    assert rows == []  # no half-provisioned row left behind
    msg = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "bastion" in msg and "bastion_" in msg  # warns and points at the env vars


async def test_seed_skips_when_no_creds_and_no_admin(factory):
    agent_id = await seed_bastion(factory, _NoCredsSettings(), SecretBox(TEST_FERNET_KEY), admin_client=None)
    assert agent_id is None
