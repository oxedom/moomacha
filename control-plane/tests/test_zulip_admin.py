import httpx
import pytest
import respx

from control_plane.services.zulip_admin import ProvisionResult, ZulipAdminClient

SITE = "https://example.zulipchat.com"


@pytest.fixture
def admin():
    return ZulipAdminClient(site=SITE, email="admin@x", api_key="adminkey")


@respx.mock
async def test_provision_bot_creates_and_returns_creds(admin):
    create = respx.post(f"{SITE}/api/v1/bots").mock(
        return_value=httpx.Response(
            200, json={"result": "success", "user_id": 77, "api_key": "bot-key"}
        )
    )
    respx.get(f"{SITE}/api/v1/users/77").mock(
        return_value=httpx.Response(
            200, json={"result": "success", "user": {"email": "researcher-bot@x"}}
        )
    )
    respx.post(f"{SITE}/api/v1/register").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "realm_bots": [
                    {"user_id": 77, "services": [{"token": "webhook-tok", "interface": 1}]}
                ],
            },
        )
    )
    sub = respx.post(f"{SITE}/api/v1/users/me/subscriptions").mock(
        return_value=httpx.Response(200, json={"result": "success"})
    )

    result = await admin.provision_bot(
        full_name="Researcher",
        short_name="researcher-bot",
        payload_url="https://tunnel/zulip/incoming",
        channels=["sandbox"],
    )

    assert create.called
    assert isinstance(result, ProvisionResult)
    assert result.bot_id == 77
    assert result.api_key == "bot-key"
    assert result.bot_email == "researcher-bot@x"
    assert result.outgoing_token == "webhook-tok"  # captured from realm_bot state
    assert sub.called  # subscription attempted for the requested channel


@respx.mock
async def test_provision_bot_no_channels_skips_subscription(admin):
    respx.post(f"{SITE}/api/v1/bots").mock(
        return_value=httpx.Response(
            200, json={"result": "success", "user_id": 78, "api_key": "k2"}
        )
    )
    respx.get(f"{SITE}/api/v1/users/78").mock(
        return_value=httpx.Response(
            200, json={"result": "success", "user": {"email": "x-bot@x"}}
        )
    )
    respx.post(f"{SITE}/api/v1/register").mock(
        return_value=httpx.Response(200, json={"result": "success", "realm_bots": []})
    )
    sub = respx.post(f"{SITE}/api/v1/users/me/subscriptions").mock(
        return_value=httpx.Response(200, json={"result": "success"})
    )

    result = await admin.provision_bot(
        full_name="X", short_name="x-bot", payload_url="https://t/zulip/incoming", channels=[]
    )

    assert result.bot_email == "x-bot@x"
    assert not sub.called


@respx.mock
async def test_provision_bot_raises_on_failure(admin):
    respx.post(f"{SITE}/api/v1/bots").mock(
        return_value=httpx.Response(400, json={"result": "error", "msg": "no"})
    )

    with pytest.raises(RuntimeError):
        await admin.provision_bot(
            full_name="Researcher",
            short_name="researcher-bot",
            payload_url="https://tunnel/zulip/incoming",
            channels=[],
        )
