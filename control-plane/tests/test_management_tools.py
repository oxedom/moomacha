import httpx
import pytest
import respx

from control_plane.db.engine import build_session_factory, create_all
from control_plane.schemas.agents import AgentCreate
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox
from control_plane.tools.management import tools as mtools
from control_plane.tools.management.context import ManagementToolContext

# Fernet key generated with Fernet.generate_key(); fixed so tests are deterministic.
TEST_FERNET_KEY = "kjsN26tcj4F3Qe7dalPMBJO2MC7sK8ZRd54LNo0mz1A="


@pytest.fixture
async def registry():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    reg = AgentRegistry(factory, SecretBox(TEST_FERNET_KEY))
    yield reg
    await engine.dispose()


def _create(name="Echo", email=None):
    return AgentCreate(
        name=name,
        persona="be helpful",
        zulip_bot_id=42,
        zulip_bot_email=email or f"{name.lower()}@x",
        zulip_api_key="botkey",
        zulip_outgoing_token="tok",
        model_id="gpt-4o",
        readable_channels=["sandbox"],
    )


async def test_resolved_agent_defaults_is_bastion_false(registry):
    await registry.create(_create(name="Echo"))
    resolved = await registry.resolve_by_bot_email("echo@x")
    assert resolved is not None
    assert resolved.is_bastion is False


def _ctx(registry, message_text="", admin_client=None):
    return ManagementToolContext(
        registry=registry,
        admin_client=admin_client,
        payload_url="https://agents.example/zulip/incoming",
        default_model="gpt-4o",
        invoking_message_text=message_text,
    )


async def test_list_agents_reports_names(registry):
    await registry.create(_create(name="Echo"))
    await registry.create(_create(name="Scout", email="scout@x"))
    out = await mtools.list_agents({}, _ctx(registry))
    assert "Echo" in out and "Scout" in out


async def test_list_agents_empty(registry):
    out = await mtools.list_agents({}, _ctx(registry))
    assert "no agents" in out.lower()


async def test_get_agent_reports_details_without_secrets(registry):
    await registry.create(_create(name="Echo"))
    out = await mtools.get_agent({"name": "Echo"}, _ctx(registry))
    assert "Echo" in out
    assert "gpt-4o" in out
    assert "botkey" not in out  # api key never surfaced
    assert "tok" not in out     # outgoing token never surfaced


async def test_get_agent_not_found(registry):
    out = await mtools.get_agent({"name": "Ghost"}, _ctx(registry))
    assert "No agent named 'Ghost'" in out


async def test_get_agent_ambiguous(registry):
    await registry.create(_create(name="review-fast", email="rf@x"))
    await registry.create(_create(name="review-slow", email="rs@x"))
    out = await mtools.get_agent({"name": "review"}, _ctx(registry))
    assert "Multiple agents match" in out


class FakeAdminClient:
    def __init__(self, outgoing_token="auto-tok"):
        from control_plane.services.zulip_admin import ProvisionResult
        self._result = ProvisionResult(
            bot_id=777, api_key="auto-key", bot_email="scout-bot@x", outgoing_token=outgoing_token
        )
        self.calls = []

    async def provision_bot(self, full_name, short_name, payload_url, channels):
        self.calls.append((full_name, short_name, payload_url, tuple(channels)))
        return self._result


async def test_create_agent_manual(registry):
    out = await mtools.create_agent(
        {
            "name": "Echo",
            "persona": "echo things",
            "zulip_bot_id": 42,
            "zulip_bot_email": "echo@x",
            "zulip_api_key": "k",
            "zulip_outgoing_token": "t",
        },
        _ctx(registry),
    )
    assert "Echo" in out and "created" in out.lower()
    assert await registry.resolve_by_bot_email("echo@x") is not None


async def test_create_agent_auto_provisions_with_token(registry):
    admin = FakeAdminClient(outgoing_token="auto-tok")
    out = await mtools.create_agent(
        {"name": "Scout", "persona": "scout", "readable_channels": ["sandbox"]},
        _ctx(registry, admin_client=admin),
    )
    assert admin.calls  # provisioning was attempted
    assert "ready" in out.lower()  # token captured -> no manual attach needed
    resolved = await registry.resolve_by_bot_email("scout-bot@x")
    assert resolved is not None
    assert resolved.zulip_outgoing_token == "auto-tok"  # captured token stored


async def test_create_agent_auto_provisions_without_token_falls_back(registry):
    admin = FakeAdminClient(outgoing_token=None)
    out = await mtools.create_agent(
        {"name": "Scout", "persona": "scout"},
        _ctx(registry, admin_client=admin),
    )
    assert "attach_bot" in out  # falls back to asking for the token
    assert await registry.resolve_by_bot_email("scout-bot@x") is not None


async def test_create_agent_result_has_no_secrets(registry):
    out = await mtools.create_agent(
        {
            "name": "Echo",
            "persona": "p",
            "zulip_bot_id": 42,
            "zulip_bot_email": "echo@x",
            "zulip_api_key": "supersecretkey",
            "zulip_outgoing_token": "supersecrettok",
        },
        _ctx(registry),
    )
    assert "supersecretkey" not in out
    assert "supersecrettok" not in out


async def test_delete_agent_refuses_without_confirmation(registry):
    await registry.create(_create(name="Echo"))
    out = await mtools.delete_agent({"name": "Echo"}, _ctx(registry, message_text="delete Echo"))
    assert "confirm Echo" in out
    assert await registry.resolve_by_bot_email("echo@x") is not None  # not deleted


async def test_delete_agent_executes_with_confirmation(registry):
    await registry.create(_create(name="Echo"))
    out = await mtools.delete_agent(
        {"name": "Echo"}, _ctx(registry, message_text="confirm Echo")
    )
    assert "deleted" in out.lower()
    assert await registry.resolve_by_bot_email("echo@x") is None


async def test_delete_agent_not_found(registry):
    out = await mtools.delete_agent({"name": "Ghost"}, _ctx(registry, message_text="confirm Ghost"))
    assert "No agent named 'Ghost'" in out


async def test_enable_agent_marks_enabled(registry):
    created = await registry.create(_create(name="Echo"))
    await registry.set_enabled(created.id, False)
    out = await mtools.enable_agent({"name": "Echo"}, _ctx(registry))
    assert "Enabled" in out
    fetched = await registry.get(created.id)
    assert fetched.enabled is True


async def test_disable_agent_requires_confirmation(registry):
    created = await registry.create(_create(name="Echo"))
    out = await mtools.disable_agent({"name": "Echo"}, _ctx(registry, message_text="disable Echo"))
    assert "confirm Echo" in out
    fetched = await registry.get(created.id)
    assert fetched.enabled is True  # untouched without confirmation


async def test_disable_agent_with_confirmation_toggles(registry):
    created = await registry.create(_create(name="Echo"))
    out = await mtools.disable_agent({"name": "Echo"}, _ctx(registry, message_text="confirm Echo"))
    assert "Disabled" in out
    fetched = await registry.get(created.id)
    assert fetched.enabled is False


async def test_update_agent_applies_fields(registry):
    created = await registry.create(_create(name="Echo"))
    out = await mtools.update_agent({"name": "Echo", "persona": "new persona"}, _ctx(registry))
    assert "Updated" in out
    fetched = await registry.get(created.id)
    assert fetched.persona == "new persona"
    assert fetched.model_id == created.model_id  # untouched


async def test_attach_bot_stores_token_and_activates(registry):
    from control_plane.schemas.agents import AgentUpdate

    created = await registry.create(_create(name="Echo"))
    await registry.update(created.id, AgentUpdate(provisioning_status="awaiting_token"))
    out = await mtools.attach_bot({"name": "Echo", "outgoing_token": "newtok"}, _ctx(registry))
    assert "Attached" in out
    resolved = await registry.resolve_by_bot_email("echo@x")
    assert resolved.zulip_outgoing_token == "newtok"
    fetched = await registry.get(created.id)
    assert fetched.provisioning_status == "active"


class _FakeAdmin:
    def __init__(self, outgoing_token=None):
        self._token = outgoing_token

    async def provision_bot(self, full_name, short_name, payload_url, channels):
        from control_plane.services.zulip_admin import ProvisionResult

        return ProvisionResult(
            bot_id=555, api_key="prov-key", bot_email="echo-bot@x", outgoing_token=self._token
        )


async def test_provision_bot_captures_token(registry):
    await registry.create(_create(name="Echo"))
    out = await mtools.provision_bot(
        {"name": "Echo"}, _ctx(registry, admin_client=_FakeAdmin(outgoing_token="prov-tok"))
    )
    assert "ready" in out.lower()
    resolved = await registry.resolve_by_bot_email("echo-bot@x")
    assert resolved.zulip_api_key == "prov-key"
    assert resolved.zulip_outgoing_token == "prov-tok"


async def test_provision_bot_without_token_falls_back(registry):
    await registry.create(_create(name="Echo"))
    out = await mtools.provision_bot({"name": "Echo"}, _ctx(registry, admin_client=_FakeAdmin()))
    assert "Provisioned" in out and "attach_bot" in out


class _SiteAdmin:
    """Minimal admin-client stand-in that just exposes the Zulip site URL."""

    def __init__(self, site):
        self.site = site


@respx.mock
async def test_set_bot_avatar_fetches_image_and_uploads_as_bot(registry):
    await registry.create(_create(name="Echo"))  # zulip_bot_email=echo@x, api_key=botkey
    site = "https://example.zulipchat.com"
    respx.get("https://img.example/a.png").mock(
        return_value=httpx.Response(200, content=b"\x89PNG\r\n", headers={"content-type": "image/png"})
    )
    upload = respx.post(f"{site}/api/v1/users/me/avatar").mock(
        return_value=httpx.Response(200, json={"result": "success", "avatar_url": f"{site}/u/1.png"})
    )

    out = await mtools.set_bot_avatar(
        {"name": "Echo", "image_url": "https://img.example/a.png"},
        _ctx(registry, admin_client=_SiteAdmin(site)),
    )

    assert upload.called
    # Uploaded as the bot itself, using its decrypted credentials.
    assert upload.calls.last.request.headers["authorization"].startswith("Basic ")
    assert b"\x89PNG" in upload.calls.last.request.content
    assert "Echo" in out and f"{site}/u/1.png" in out


async def test_set_bot_avatar_requires_image_url(registry):
    await registry.create(_create(name="Echo"))
    out = await mtools.set_bot_avatar({"name": "Echo"}, _ctx(registry, admin_client=_SiteAdmin("x")))
    assert "image_url" in out


async def test_set_bot_avatar_unknown_agent(registry):
    out = await mtools.set_bot_avatar(
        {"name": "Ghost", "image_url": "https://img.example/a.png"},
        _ctx(registry, admin_client=_SiteAdmin("x")),
    )
    assert "No agent named 'Ghost'" in out
