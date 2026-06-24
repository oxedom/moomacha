import asyncio
import json
import time
import uuid

import httpx
import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

pytestmark = [pytest.mark.e2e, pytest.mark.live_e2e]


class LiveAgentE2ESettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    run_live_agent_e2e: bool = False
    control_plane_url: str = "http://127.0.0.1:8000"
    zulip_site: str | None = None
    zulip_admin_email: str | None = None
    zulip_admin_api_key: str | None = None
    zulip_bot_id: int | None = None
    zulip_bot_email: str | None = None
    zulip_bot_api_key: str | None = None
    zulip_outgoing_token: str | None = None
    e2e_agent_name: str = "live-e2e-agent"
    e2e_agent_model: str = "gpt-4o"
    e2e_zulip_mention_name: str | None = None
    e2e_zulip_channel: str = "sandbox"
    e2e_timeout_seconds: float = 90.0
    e2e_cleanup_zulip_topic: bool = True
    e2e_cleanup_created_agent: bool = True


def _missing(settings: LiveAgentE2ESettings) -> list[str]:
    required = [
        "zulip_site",
        "zulip_admin_email",
        "zulip_admin_api_key",
        "zulip_bot_id",
        "zulip_bot_email",
        "zulip_bot_api_key",
        "zulip_outgoing_token",
    ]
    missing = [name.upper() for name in required if not getattr(settings, name)]
    if not settings.run_live_agent_e2e:
        missing.insert(0, "RUN_LIVE_AGENT_E2E=1")
    return missing


async def _ensure_registered(
    http: httpx.AsyncClient,
    settings: LiveAgentE2ESettings,
) -> tuple[str | None, bool]:
    control = settings.control_plane_url.rstrip("/")
    payload = {
        "name": settings.e2e_agent_name,
        "persona": (
            "You are the live e2e test agent. Reply with one short sentence "
            "that acknowledges the request."
        ),
        "model_id": settings.e2e_agent_model,
        "context_message_count": 10,
        "readable_channels": [settings.e2e_zulip_channel],
        "zulip_bot_id": settings.zulip_bot_id,
        "zulip_bot_email": settings.zulip_bot_email,
        "zulip_api_key": settings.zulip_bot_api_key,
        "zulip_outgoing_token": settings.zulip_outgoing_token,
    }
    resp = await http.post(f"{control}/agents", json=payload)
    if resp.status_code == 201:
        return resp.json()["id"], True
    if resp.status_code != 409:
        raise AssertionError(f"agent registration failed: {resp.status_code} {resp.text}")

    listed = await http.get(f"{control}/agents")
    listed.raise_for_status()
    if not any(a.get("zulip_bot_email") == settings.zulip_bot_email for a in listed.json()):
        raise AssertionError(
            "agent registration conflicted, but no existing agent uses the configured bot email"
        )
    return None, False


async def _delete_created_agent(
    http: httpx.AsyncClient,
    settings: LiveAgentE2ESettings,
    agent_id: str,
) -> None:
    control = settings.control_plane_url.rstrip("/")
    resp = await http.delete(f"{control}/agents/{agent_id}")
    if resp.status_code not in {204, 404}:
        raise AssertionError(f"failed to delete e2e agent row: {resp.status_code} {resp.text}")


async def _send_zulip_message(settings: LiveAgentE2ESettings, topic: str, content: str) -> int:
    async with httpx.AsyncClient(
        auth=(settings.zulip_admin_email, settings.zulip_admin_api_key),
        timeout=30,
    ) as http:
        resp = await http.post(
            f"{settings.zulip_site.rstrip('/')}/api/v1/messages",
            data={
                "type": "stream",
                "to": settings.e2e_zulip_channel,
                "topic": topic,
                "content": content,
            },
        )
        if resp.status_code != 200:
            raise AssertionError(f"failed to post Zulip trigger: {resp.status_code} {resp.text}")
        return int(resp.json()["id"])


async def _topic_messages(
    http: httpx.AsyncClient,
    settings: LiveAgentE2ESettings,
    topic: str,
) -> list[dict]:
    resp = await http.get(
        f"{settings.zulip_site.rstrip('/')}/api/v1/messages",
        params={
            "anchor": "newest",
            "num_before": 50,
            "num_after": 0,
            "narrow": json.dumps(
                [
                    {"operator": "stream", "operand": settings.e2e_zulip_channel},
                    {"operator": "topic", "operand": topic},
                ]
            ),
            "apply_markdown": "false",
        },
    )
    resp.raise_for_status()
    return list(resp.json()["messages"])


async def _stream_id(http: httpx.AsyncClient, settings: LiveAgentE2ESettings) -> int:
    resp = await http.get(
        f"{settings.zulip_site.rstrip('/')}/api/v1/get_stream_id",
        params={"stream": settings.e2e_zulip_channel},
    )
    resp.raise_for_status()
    return int(resp.json()["stream_id"])


async def _delete_topic(settings: LiveAgentE2ESettings, topic: str) -> None:
    async with httpx.AsyncClient(
        auth=(settings.zulip_admin_email, settings.zulip_admin_api_key),
        timeout=30,
    ) as http:
        stream_id = await _stream_id(http, settings)
        for _ in range(3):
            resp = await http.post(
                f"{settings.zulip_site.rstrip('/')}/api/v1/streams/{stream_id}/delete_topic",
                data={"topic_name": topic},
            )
            if resp.status_code == 400 and "No messages" in resp.text:
                return
            if resp.status_code != 200:
                raise AssertionError(
                    f"failed to delete Zulip e2e topic: {resp.status_code} {resp.text}"
                )
            body = resp.json()
            if body.get("result") != "success":
                raise AssertionError(f"failed to delete Zulip e2e topic: {body}")
            if body.get("complete", True):
                return
        raise AssertionError(f"Zulip e2e topic cleanup did not complete for {topic!r}")


def _has_plus_one(message: dict) -> bool:
    for reaction in message.get("reactions") or []:
        if reaction.get("emoji_name") in {"+1", "thumbs_up"}:
            return True
        if reaction.get("emoji_code") == "1f44d":
            return True
    return False


async def _wait_for_reply(
    settings: LiveAgentE2ESettings,
    topic: str,
    trigger_id: int,
) -> tuple[dict, dict]:
    deadline = time.monotonic() + settings.e2e_timeout_seconds
    last_messages: list[dict] = []
    async with httpx.AsyncClient(
        auth=(settings.zulip_admin_email, settings.zulip_admin_api_key),
        timeout=30,
    ) as http:
        while time.monotonic() < deadline:
            last_messages = await _topic_messages(http, settings, topic)
            trigger = next((m for m in last_messages if m.get("id") == trigger_id), None)
            replies = [
                m
                for m in last_messages
                if m.get("id", 0) > trigger_id
                and m.get("sender_email") == settings.zulip_bot_email
                and "Working on it" not in (m.get("content") or "")
            ]
            if trigger is not None and _has_plus_one(trigger) and replies:
                return trigger, replies[-1]
            await asyncio.sleep(2)
    raise AssertionError(
        "timed out waiting for live agent reply "
        f"after seeing {len(last_messages)} messages in topic {topic!r}"
    )


async def test_live_zulip_mention_runs_agent_turn_end_to_end():
    settings = LiveAgentE2ESettings()
    missing = _missing(settings)
    if missing:
        pytest.skip("live e2e disabled or missing env: " + ", ".join(missing))

    created_agent_id: str | None = None
    topic_created = False
    run_id = uuid.uuid4().hex[:8]
    mention_name = settings.e2e_zulip_mention_name or settings.e2e_agent_name
    topic = f"agent-e2e-{run_id}"

    try:
        control = settings.control_plane_url.rstrip("/")
        async with httpx.AsyncClient(timeout=30) as http:
            health = await http.get(f"{control}/healthz")
            health.raise_for_status()
            created_agent_id, _ = await _ensure_registered(http, settings)

        content = (
            f"@**{mention_name}** live e2e ping {run_id}. "
            "Reply with one short sentence."
        )
        trigger_id = await _send_zulip_message(settings, topic, content)
        topic_created = True
        trigger, reply = await _wait_for_reply(settings, topic, trigger_id)

        assert trigger["id"] == trigger_id
        assert reply["sender_email"] == settings.zulip_bot_email
        assert reply.get("content")
    finally:
        if settings.e2e_cleanup_zulip_topic and topic_created:
            await _delete_topic(settings, topic)
        if settings.e2e_cleanup_created_agent and created_agent_id is not None:
            async with httpx.AsyncClient(timeout=30) as http:
                await _delete_created_agent(http, settings, created_agent_id)
