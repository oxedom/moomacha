import httpx
import pytest
import respx

from control_plane.zulip_avatar import set_bot_avatar

SITE = "https://example.zulipchat.com"


@respx.mock
async def test_set_bot_avatar_uploads_file_and_returns_url():
    route = respx.post(f"{SITE}/api/v1/users/me/avatar").mock(
        return_value=httpx.Response(
            200, json={"result": "success", "msg": "", "avatar_url": f"{SITE}/user_avatars/2.png"}
        )
    )

    url = await set_bot_avatar(
        site=SITE, email="echo-bot@x", api_key="key123",
        image_bytes=b"\x89PNG\r\n\x1a\n", filename="bot.png",
    )

    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"].startswith("Basic ")
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="file"' in req.content
    assert b"bot.png" in req.content
    assert b"\x89PNG" in req.content
    assert url == f"{SITE}/user_avatars/2.png"


@respx.mock
async def test_set_bot_avatar_raises_on_error():
    respx.post(f"{SITE}/api/v1/users/me/avatar").mock(
        return_value=httpx.Response(400, json={"result": "error", "msg": "bad image"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await set_bot_avatar(
            site=SITE, email="echo-bot@x", api_key="key123", image_bytes=b"nope"
        )
