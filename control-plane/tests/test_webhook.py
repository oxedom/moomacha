import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.routes.zulip_webhook import build_webhook_router
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.job_queue import Job


def _agent():
    return ResolvedAgent(
        id=uuid.uuid4(),
        name="echo",
        persona="p",
        model_id="gpt-4o",
        zulip_bot_id=42,
        zulip_bot_email="echo-bot@x",
        zulip_api_key="k",
        zulip_outgoing_token="tok123",
        context_message_count=20,
        readable_channels=["sandbox"],
    )


@pytest.fixture
def payload():
    return {
        "token": "tok123",
        "bot_email": "echo-bot@x",
        "trigger": "mention",
        "message": {
            "id": 112,
            "content": "@**echo** hi",
            "display_recipient": "sandbox",
            "subject": "greetings",
            "type": "stream",
        },
    }


async def _noop_event(**kwargs):
    return None


def build(enqueued, resolved_agent, reactions):
    async def resolve(bot_email):
        return resolved_agent if bot_email == "echo-bot@x" else None

    class FakeClient:
        def __init__(self, email, key):
            pass

        async def add_reaction(self, mid, emoji):
            reactions.append((mid, emoji))

    async def enqueue(job):
        enqueued.append(job)

    app = FastAPI()
    app.include_router(
        build_webhook_router(
            resolve_agent_by_email=resolve,
            make_agent_client=lambda e, k: FakeClient(e, k),
            enqueue_job=enqueue,
            write_event=_noop_event,
        )
    )
    return TestClient(app)


def test_valid_mention_reacts_and_enqueues(payload):
    enqueued, reactions = [], []
    client = build(enqueued, _agent(), reactions)

    resp = client.post("/zulip/incoming", json=payload)

    assert resp.status_code == 200
    assert reactions == [(112, "+1")]
    assert len(enqueued) == 1
    assert isinstance(enqueued[0], Job)
    assert enqueued[0].channel == "sandbox"
    assert enqueued[0].topic == "greetings"
    assert enqueued[0].conversation_type == "stream"


def test_direct_message_reacts_and_enqueues_direct_job(payload):
    payload["message"] = {
        "id": 113,
        "content": "hi bot",
        "display_recipient": [
            {"id": 7, "email": "alice@example.test", "full_name": "Alice"},
            {"id": 42, "email": "echo-bot@x", "full_name": "Echo"},
        ],
        "subject": "",
        "type": "private",
        "sender_id": 7,
        "sender_email": "alice@example.test",
    }
    enqueued, reactions = [], []
    client = build(enqueued, _agent(), reactions)

    resp = client.post("/zulip/incoming", json=payload)

    assert resp.status_code == 200
    assert reactions == [(113, "+1")]
    assert len(enqueued) == 1
    assert enqueued[0].conversation_type == "direct"
    assert enqueued[0].channel == "direct"
    assert enqueued[0].topic == ""
    assert enqueued[0].direct_recipient_ids == [7]


def test_unknown_bot_is_noop(payload):
    payload["bot_email"] = "nobody@x"
    enqueued, reactions = [], []
    client = build(enqueued, _agent(), reactions)

    resp = client.post("/zulip/incoming", json=payload)

    assert resp.status_code == 200
    assert enqueued == []
    assert reactions == []


def test_bad_token_rejected(payload):
    payload["token"] = "wrong"
    enqueued, reactions = [], []
    client = build(enqueued, _agent(), reactions)

    resp = client.post("/zulip/incoming", json=payload)

    assert resp.status_code == 403
    assert enqueued == []


def test_duplicate_enqueues_once(payload):
    enqueued, reactions = [], []
    client = build(enqueued, _agent(), reactions)

    client.post("/zulip/incoming", json=payload)
    client.post("/zulip/incoming", json=payload)

    assert len(enqueued) == 1


def test_healthz():
    client = build([], _agent(), [])
    assert client.get("/healthz").status_code == 200
