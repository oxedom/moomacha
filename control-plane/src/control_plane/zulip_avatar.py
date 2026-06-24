"""Standalone helper for setting a Zulip bot's own avatar.

Zulip's REST API only lets a user/bot set *its own* avatar — there is no
admin-set-other-user endpoint — so this authenticates as the target bot
and uploads the image to ``POST /api/v1/users/me/avatar``.
"""

import httpx


async def set_bot_avatar(
    site: str,
    email: str,
    api_key: str,
    image_bytes: bytes,
    filename: str = "avatar.png",
    content_type: str = "image/png",
) -> str:
    """Upload ``image_bytes`` as the avatar of the bot identified by ``email``/``api_key``.

    Returns the new ``avatar_url`` from Zulip. Raises ``httpx.HTTPStatusError``
    on a non-2xx response.
    """
    base = site.rstrip("/")
    async with httpx.AsyncClient(auth=(email, api_key)) as http:
        resp = await http.post(
            f"{base}/api/v1/users/me/avatar",
            files={"file": (filename, image_bytes, content_type)},
        )
        resp.raise_for_status()
        return resp.json()["avatar_url"]
