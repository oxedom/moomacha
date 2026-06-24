import asyncio
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport
from sqlalchemy import select

import control_plane.app as app_module
from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.db.tables import EventRow
from control_plane.services.zulip_admin import ProvisionResult

pytestmark = pytest.mark.e2e


class FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.create_calls: list[dict] = []
        self.closed = False

    @property
    def chat(self):
        return SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        msg = SimpleNamespace(content=self.text, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    async def close(self) -> None:
        self.closed = True


class FakeZulipBus:
    def __init__(self) -> None:
        self.reactions: list[tuple[str, int, str]] = []
        self.sent: list[dict] = []
        self.updated: list[tuple[int, str]] = []
        self.history_requests: list[tuple[str, str, str, int]] = []
        self.next_message_id = 7000

    def next_id(self) -> int:
        self.next_message_id += 1
        return self.next_message_id


def _fake_zulip_client(bus: FakeZulipBus):
    class FakeZulipClient:
        def __init__(self, site: str, email: str, api_key: str) -> None:
            self.site = site
            self.email = email
            self.api_key = api_key

        async def add_reaction(self, message_id: int, emoji_name: str) -> None:
            bus.reactions.append((self.email, message_id, emoji_name))

        async def send_message(self, channel: str, topic: str, content: str) -> int:
            message_id = bus.next_id()
            bus.sent.append(
                {
                    "id": message_id,
                    "sender_email": self.email,
                    "channel": channel,
                    "topic": topic,
                    "content": content,
                }
            )
            return message_id

        async def get_messages(self, channel: str, topic: str, num_before: int) -> list[dict]:
            bus.history_requests.append((self.email, channel, topic, num_before))
            return [
                {"sender_full_name": "Alice", "content": "@**local-e2e** please answer"},
                {"sender_full_name": "Local E2E", "content": "Earlier bot context"},
            ]

        async def get_channel_messages(self, channel: str, num_before: int) -> list[dict]:
            return [{"sender_full_name": "Alice", "content": "channel context"}]

        async def update_message(self, message_id: int, content: str) -> None:
            bus.updated.append((message_id, content))

    return FakeZulipClient


class FakeAdminClient:
    def __init__(self, site: str, email: str, api_key: str) -> None:
        pass

    async def provision_bot(
        self,
        full_name: str,
        short_name: str,
        payload_url: str,
        channels: list[str],
    ) -> ProvisionResult:
        return ProvisionResult(
            bot_id=1,
            api_key="bastion-api-key",
            bot_email="bastion-bot@example.test",
            outgoing_token="bastion-token",
        )


def _settings(db_url: str) -> Settings:
    return Settings(
        _env_file=None,
        zulip_site="https://zulip.example.test",
        neon_database_url=db_url,
        openai_key="sk-test",
        agent_fernet_key=Fernet.generate_key().decode(),
        job_worker_count=1,
        schedule_poll_interval_seconds=3600,
    )


async def _wait_for(predicate, *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


async def _events(db_url: str) -> list[EventRow]:
    factory, engine = build_session_factory(db_url)
    try:
        async with factory() as session:
            rows = (
                await session.execute(select(EventRow).order_by(EventRow.timestamp))
            ).scalars().all()
            return list(rows)
    finally:
        await engine.dispose()


async def _wait_for_event(
    db_url: str,
    *,
    message_id: int,
    event_type: str,
    timeout: float = 2.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        rows = await _events(db_url)
        if any(e.source_message_id == message_id and e.event_type == event_type for e in rows):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"event {event_type!r} was not persisted before timeout")


async def test_registered_agent_replies_to_webhook_through_real_app_worker_and_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'agent-e2e.db'}"
    final_text = "Final agent answer from the model."
    fake_llm = FakeLLM(final_text)
    zulip = FakeZulipBus()

    monkeypatch.setattr(app_module, "ZulipClient", _fake_zulip_client(zulip))
    monkeypatch.setattr(app_module, "ZulipAdminClient", FakeAdminClient)
    monkeypatch.setattr(app_module, "default_client_factory", lambda api_key, base_url: fake_llm)

    app = app_module.create_app(_settings(db_url))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            created = await client.post(
                "/agents",
                json={
                    "name": "local-e2e",
                    "persona": "You are a concise test agent.",
                    "model_id": "gpt-test",
                    "context_message_count": 7,
                    "readable_channels": ["sandbox"],
                    "zulip_bot_id": 42,
                    "zulip_bot_email": "local-e2e-bot@example.test",
                    "zulip_api_key": "bot-api-key",
                    "zulip_outgoing_token": "outgoing-token",
                },
            )
            assert created.status_code == 201
            assert "zulip_api_key" not in created.json()
            assert "zulip_outgoing_token" not in created.json()

            message_id = 9001
            webhook = await client.post(
                "/zulip/incoming",
                json={
                    "token": "outgoing-token",
                    "bot_email": "local-e2e-bot@example.test",
                    "trigger": "mention",
                    "message": {
                        "id": message_id,
                        "content": "@**local-e2e** please answer",
                        "display_recipient": "sandbox",
                        "subject": "agent-e2e",
                        "type": "stream",
                        "sender_email": "alice@example.test",
                    },
                },
            )
            assert webhook.status_code == 200

            await _wait_for(lambda: len(zulip.updated) == 1)
            await _wait_for_event(db_url, message_id=message_id, event_type="turn.end")

    progress_id = zulip.sent[0]["id"]
    assert zulip.reactions == [("local-e2e-bot@example.test", message_id, "+1")]
    assert "Working on it" in zulip.sent[0]["content"]
    assert zulip.updated == [(progress_id, final_text)]
    assert zulip.history_requests == [
        ("local-e2e-bot@example.test", "sandbox", "agent-e2e", 7)
    ]
    assert fake_llm.closed is True

    llm_call = fake_llm.create_calls[0]
    assert llm_call["model"] == "gpt-test"
    assert llm_call["messages"][0]["role"] == "system"
    assert "You are a concise test agent." in llm_call["messages"][0]["content"]
    assert "Alice: @**local-e2e** please answer" in llm_call["messages"][0]["content"]
    assert llm_call["messages"][1] == {
        "role": "user",
        "content": "@**local-e2e** please answer",
    }

    rows = [e for e in await _events(db_url) if e.source_message_id == message_id]
    assert [e.event_type for e in rows] == [
        "webhook_received",
        "turn.start",
        "turn.end",
    ]
    assert rows[-1].payload == {"reply": final_text}
