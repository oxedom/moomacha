import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from control_plane.routes.zulip_webhook import build_webhook_router
from control_plane.schemas.agents import ResolvedAgent


def _resolved(name, email, *, is_bastion=False, token="tok"):
    return ResolvedAgent(
        id=uuid.uuid4(),
        name=name,
        persona="p",
        model_id="gpt-4o",
        zulip_bot_email=email,
        zulip_api_key="k",
        zulip_outgoing_token=token,
        context_message_count=20,
        readable_channels=[],
        is_bastion=is_bastion,
    )


class FakeClient:
    def __init__(self):
        self.reactions = []

    async def add_reaction(self, message_id, emoji):
        self.reactions.append((message_id, emoji))


def _build(agents_by_email, enqueued, events):
    async def resolve(email):
        return agents_by_email.get(email)

    async def enqueue(job):
        enqueued.append(job)

    async def write_event(**kwargs):
        events.append(kwargs)

    app = FastAPI()
    app.include_router(
        build_webhook_router(
            resolve_agent_by_email=resolve,
            make_agent_client=lambda email, key: FakeClient(),
            enqueue_job=enqueue,
            write_event=write_event,
        )
    )
    return app


def _payload(bot_email, sender_email, token="tok", message_id=1):
    return {
        "token": token,
        "bot_email": bot_email,
        "message": {
            "id": message_id,
            "content": "@**Bastion** delete Echo",
            "display_recipient": "sandbox",
            "subject": "ops",
            "sender_email": sender_email,
        },
    }


@pytest.fixture
def bastion():
    return _resolved("Bastion", "bastion@x", is_bastion=True)


async def test_human_can_invoke_bastion(bastion):
    enqueued, events = [], []
    app = _build({"bastion@x": bastion}, enqueued, events)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/zulip/incoming", json=_payload("bastion@x", "human@people.com"))
    assert resp.status_code == 200
    assert len(enqueued) == 1


async def test_agent_cannot_invoke_bastion(bastion):
    echo = _resolved("Echo", "echo@x")
    enqueued, events = [], []
    app = _build({"bastion@x": bastion, "echo@x": echo}, enqueued, events)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/zulip/incoming", json=_payload("bastion@x", "echo@x"))
    assert resp.status_code == 200
    assert enqueued == []  # blocked
    assert any(e.get("event_type") == "bastion_invocation_blocked" for e in events)


async def test_agent_can_invoke_ordinary_agent():
    target = _resolved("Echo", "echo@x")
    sender = _resolved("Scout", "scout@x")
    enqueued, events = [], []
    app = _build({"echo@x": target, "scout@x": sender}, enqueued, events)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/zulip/incoming", json=_payload("echo@x", "scout@x"))
    assert resp.status_code == 200
    assert len(enqueued) == 1  # gate only fires for the bastion
