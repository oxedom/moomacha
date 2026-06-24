import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from control_plane.zulip_client import ZulipClient

SITE = "https://example.zulipchat.com"


@pytest.fixture
def client():
    return ZulipClient(site=SITE, email="echo-bot@x", api_key="key123")


@respx.mock
async def test_add_reaction_posts_correct_request(client):
    route = respx.post(f"{SITE}/api/v1/messages/112/reactions").mock(
        return_value=httpx.Response(200, json={"result": "success", "msg": ""})
    )

    await client.add_reaction(112, "+1")

    assert route.called
    req = route.calls.last.request
    assert b"emoji_name=%2B1" in req.content or b"emoji_name=+1" in req.content
    assert req.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_send_message_posts_correct_request(client):
    route = respx.post(f"{SITE}/api/v1/messages").mock(
        return_value=httpx.Response(200, json={"result": "success", "id": 999})
    )

    await client.send_message(channel="sandbox", topic="greetings", content="You said: hi")

    assert route.called
    body = route.calls.last.request.content
    assert b"type=stream" in body
    assert b"to=sandbox" in body
    assert b"topic=greetings" in body
    assert b"You+said" in body or b"You%20said" in body


@respx.mock
async def test_send_message_returns_id(client):
    respx.post(f"{SITE}/api/v1/messages").mock(
        return_value=httpx.Response(200, json={"result": "success", "id": 999})
    )

    message_id = await client.send_message(channel="sandbox", topic="t", content="hi")

    assert message_id == 999


@respx.mock
async def test_upload_file_posts_multipart_with_auth(client):
    route = respx.post(f"{SITE}/api/v1/user_uploads").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "msg": "",
                "filename": "claw-test.png",
                "url": "/user_uploads/1/a/claw-test.png",
                "uri": "/user_uploads/1/a/claw-test.png",
            },
        )
    )

    result = await client.upload_file(
        filename="claw-test.png",
        content=b"png-bytes",
        content_type="image/png",
    )

    assert result["url"] == "/user_uploads/1/a/claw-test.png"
    req = route.calls.last.request
    assert req.headers["authorization"].startswith("Basic ")
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="filename"; filename="claw-test.png"' in req.content
    assert b"png-bytes" in req.content


@respx.mock
async def test_send_direct_message_posts_correct_request(client):
    route = respx.post(f"{SITE}/api/v1/messages").mock(
        return_value=httpx.Response(200, json={"result": "success", "id": 1001})
    )

    message_id = await client.send_direct_message(recipient_ids=[7], content="DM answer")

    assert message_id == 1001
    body = route.calls.last.request.content
    assert b"type=direct" in body
    assert b"to=%5B7%5D" in body or b"to=[7]" in body
    assert b"DM+answer" in body or b"DM%20answer" in body


@respx.mock
async def test_get_messages_uses_narrow(client):
    route = respx.get(f"{SITE}/api/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={"result": "success", "messages": [{"id": 1, "content": "hi", "sender_full_name": "A"}]},
        )
    )

    messages = await client.get_messages(channel="sandbox", topic="greetings", num_before=20)

    assert route.called
    req = route.calls.last.request
    assert b"sandbox" in req.url.query
    assert messages[0]["content"] == "hi"


@respx.mock
async def test_get_direct_messages_uses_dm_narrow(client):
    route = respx.get(f"{SITE}/api/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={"result": "success", "messages": [{"id": 2, "content": "dm"}]},
        )
    )

    messages = await client.get_direct_messages(recipient_ids=[7], num_before=10)

    assert route.called
    query = parse_qs(route.calls.last.request.url.query.decode())
    narrow = json.loads(query["narrow"][0])
    assert narrow == [{"operator": "dm", "operand": [7]}]
    assert messages[0]["content"] == "dm"


@respx.mock
async def test_update_message_patches(client):
    route = respx.patch(f"{SITE}/api/v1/messages/55").mock(
        return_value=httpx.Response(200, json={"result": "success"})
    )

    await client.update_message(55, "final answer")

    assert route.called
    assert b"final+answer" in route.calls.last.request.content or b"final%20answer" in route.calls.last.request.content
