"""Shared Google OAuth2 client for the in-process Calendar + Tasks tools.

One installed-app OAuth client (client_id/secret) plus a long-lived refresh
token grants access to both the Calendar v3 and Tasks v1 REST APIs (the token is
minted once, locally, with the ``calendar`` + ``tasks`` scopes). The control
plane is a plain REST client here — same shape as ``AgentMemoryRest`` — so the
calendar/tasks adapters and their tests never need the network.

Access tokens are short-lived (~1h); this client refreshes them lazily, caches
the result in memory, and force-refreshes once on a 401. The refresh token does
not expire because the OAuth consent screen is published to *production* (a
Testing-mode app would expire it after 7 days).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("control_plane")

_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Refresh a little before the real expiry so an in-flight call never races the
# boundary.
_EXPIRY_SKEW_SECONDS = 60.0


class GoogleApiError(RuntimeError):
    """A Google REST call failed; message is safe to surface to the agent."""


class GoogleClient:
    """Async REST client that keeps a fresh Google access token.

    Injectable: tests construct it and monkeypatch ``_http`` / token state rather
    than hitting Google.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        timeout: float = 20.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._timeout = timeout
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(_TOKEN_URL, data=data)
        if r.status_code != 200:
            raise GoogleApiError(
                f"Google token refresh failed ({r.status_code}): {r.text[:300]}"
            )
        body = r.json()
        self._access_token = body["access_token"]
        # expires_in is seconds; default to 1h if Google omits it.
        self._expires_at = time.monotonic() + float(body.get("expires_in", 3600)) - _EXPIRY_SKEW_SECONDS

    async def _token(self, *, force: bool = False) -> str:
        async with self._lock:
            if force or self._access_token is None or time.monotonic() >= self._expires_at:
                await self._refresh()
            assert self._access_token is not None
            return self._access_token

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Make an authed request, refreshing once on a 401. Returns parsed JSON
        (or ``{}`` for an empty 2xx body, e.g. a DELETE)."""
        # Drop None-valued params so callers can pass optionals uniformly.
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        token = await self._token()
        body = await self._send(method, url, token, clean_params, json)
        if body is _UNAUTHORIZED:
            token = await self._token(force=True)
            body = await self._send(method, url, token, clean_params, json)
        if body is _UNAUTHORIZED:
            raise GoogleApiError("Google rejected the credentials (401) after a token refresh.")
        return body

    async def _send(
        self,
        method: str,
        url: str,
        token: str,
        params: dict[str, Any],
        json: dict[str, Any] | None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.request(method, url, params=params, json=json, headers=headers)
        if r.status_code == 401:
            return _UNAUTHORIZED
        if r.status_code >= 400:
            raise GoogleApiError(f"Google API {method} {url} -> {r.status_code}: {r.text[:400]}")
        if not r.content:
            return {}
        return r.json()


# Sentinel distinguishing "got a 401, retry" from a legitimate JSON body.
_UNAUTHORIZED = object()
