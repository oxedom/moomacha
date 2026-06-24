import httpx
import pytest
import respx

from control_plane.services.zulip_admin import ZulipAdminClient
from control_plane.zulip_client import ZulipClient

SITE = "https://example.zulipchat.com"


@pytest.fixture
def admin():
    return ZulipAdminClient(site=SITE, email="admin@x", api_key="adminkey")


@pytest.fixture
def bot_client():
    return ZulipClient(site=SITE, email="worker1@x", api_key="k")


@respx.mock
async def test_rename_bot_patches_correct_endpoint(admin):
    patch = respx.patch(f"{SITE}/api/v1/bots/42").mock(
        return_value=httpx.Response(200, json={"result": "success"})
    )
    await admin.rename_bot(42, "Scout")
    assert patch.called
    request_body = patch.calls[0].request.content.decode()
    assert "Scout" in request_body


@respx.mock
async def test_rename_bot_raises_on_error(admin):
    respx.patch(f"{SITE}/api/v1/bots/42").mock(
        return_value=httpx.Response(400, json={"result": "error", "msg": "no"})
    )
    with pytest.raises(Exception):
        await admin.rename_bot(42, "Scout")


@respx.mock
async def test_subscribe_to_channel_posts_subscriptions(bot_client):
    sub = respx.post(f"{SITE}/api/v1/users/me/subscriptions").mock(
        return_value=httpx.Response(200, json={"result": "success"})
    )
    await bot_client.subscribe_to_channel("sandbox")
    assert sub.called
    request_body = sub.calls[0].request.content.decode()
    assert "sandbox" in request_body
