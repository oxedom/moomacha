import json
from dataclasses import dataclass

import httpx


@dataclass
class ProvisionResult:
    bot_id: int
    api_key: str
    bot_email: str
    outgoing_token: str | None = None  # the bot's webhook-verification token, if captured


class ZulipAdminClient:
    """Creates outgoing-webhook bots using an admin API key.

    Uses the REST /api/v1/bots endpoint, which accepts admin API-key basic auth.
    (The /json/bots web-app endpoint requires a logged-in session + CSRF token and
    rejects API-key auth with "Not logged in".) Bot creation does not return the
    outgoing-webhook token directly, but it is recoverable via /api/v1/register
    (the realm_bot state carries each bot's services[].token), so provision_bot
    captures it and the bot is webhook-ready with no manual attach step.
    """

    def __init__(self, site: str, email: str, api_key: str) -> None:
        self._base = site.rstrip("/")
        self._auth = (email, api_key)

    @property
    def site(self) -> str:
        """The normalized Zulip site base URL (no trailing slash)."""
        return self._base

    async def provision_bot(
        self,
        full_name: str,
        short_name: str,
        payload_url: str,
        channels: list[str],
    ) -> ProvisionResult:
        async with httpx.AsyncClient(auth=self._auth) as http:
            resp = await http.post(
                f"{self._base}/api/v1/bots",
                data={
                    "full_name": full_name,
                    "short_name": short_name,
                    "bot_type": 3,  # OUTGOING_WEBHOOK
                    "payload_url": json.dumps(payload_url),
                    "interface_type": 1,  # Zulip
                },
            )
            body = resp.json()
            if resp.status_code != 200 or body.get("result") != "success":
                raise RuntimeError(f"Bot provisioning failed: {body.get('msg', resp.text)}")

            bot_id = body["user_id"]
            api_key = body.get("api_key")
            if not api_key:
                key_resp = await http.get(f"{self._base}/api/v1/bots/{bot_id}/api_key")
                api_key = key_resp.json()["api_key"]

            # Always resolve the canonical bot email so the caller can match it
            # against future webhook `bot_email` values.
            bot_email = await self._bot_email(bot_id, http)

            # Recover the outgoing-webhook token (not returned by bot creation) from
            # the realm_bot state so the bot can pass webhook token validation.
            outgoing_token = await self._outgoing_token(bot_id, http)

            # Subscribe the new bot to its readable channels (acting as the bot).
            if channels:
                bot_auth = (bot_email, api_key)
                async with httpx.AsyncClient(auth=bot_auth) as bot_http:
                    await bot_http.post(
                        f"{self._base}/api/v1/users/me/subscriptions",
                        data={"subscriptions": json.dumps([{"name": c} for c in channels])},
                    )

            return ProvisionResult(
                bot_id=bot_id,
                api_key=api_key,
                bot_email=bot_email,
                outgoing_token=outgoing_token,
            )

    async def _bot_email(self, bot_id: int, http: httpx.AsyncClient) -> str:
        resp = await http.get(f"{self._base}/api/v1/users/{bot_id}")
        return resp.json()["user"]["email"]

    async def _outgoing_token(self, bot_id: int, http: httpx.AsyncClient) -> str | None:
        """Fetch the bot's outgoing-webhook token from realm_bot state, matched by
        user_id. Returns None if it can't be found (caller falls back to attach)."""
        resp = await http.post(
            f"{self._base}/api/v1/register",
            data={"fetch_event_types": json.dumps(["realm_bot"])},
        )
        if resp.status_code != 200:
            return None
        for bot in resp.json().get("realm_bots", []):
            if bot.get("user_id") == bot_id:
                services = bot.get("services") or []
                if services:
                    return services[0].get("token")
        return None

    async def rename_bot(self, bot_id: int, full_name: str) -> None:
        async with httpx.AsyncClient(auth=self._auth) as http:
            resp = await http.patch(
                f"{self._base}/api/v1/bots/{bot_id}",
                data={"full_name": full_name},
            )
            resp.raise_for_status()
