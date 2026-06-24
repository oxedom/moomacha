import uuid
from dataclasses import dataclass

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from control_plane.routes.zulip_webhook import _token_matches, build_webhook_router
from control_plane.schemas.agents import ResolvedAgent


# ---- _token_matches unit behavior -------------------------------------------

def test_token_matches_rejects_empty_expected():
    # An agent/pool bot with a blank outgoing token must never authenticate,
    # even if the attacker also sends an empty token.
    assert _token_matches("", "") is False
    assert _token_matches("anything", "") is False


def test_token_matches_rejects_none_expected():
    assert _token_matches("anything", None) is False


def test_token_matches_accepts_exact_match():
    assert _token_matches("tok123", "tok123") is True


def test_token_matches_rejects_mismatch():
    assert _token_matches("wrong", "tok123") is False


# ---- webhook integration ----------------------------------------------------

def _resolved(email, *, token):
    return ResolvedAgent(
        id=uuid.uuid4(),
        name="echo",
        persona="p",
        model_id="gpt-4o",
        zulip_bot_id=42,
        zulip_bot_email=email,
        zulip_api_key="k",
        zulip_outgoing_token=token,
        context_message_count=20,
        readable_channels=["sandbox"],
    )


class FakeClient:
    def __init__(self, *_args):
        self.reactions = []

    async def add_reaction(self, message_id, emoji):
        self.reactions.append((message_id, emoji))


@dataclass
class FakePoolResolution:
    outgoing_token: str
    agent: ResolvedAgent
    session_id: uuid.UUID


def _build(agents_by_email, enqueued, *, pool_resolution=None):
    async def resolve(email):
        return agents_by_email.get(email)

    async def enqueue(job):
        enqueued.append(job)

    async def write_event(**kwargs):
        return None

    async def resolve_pool_bot_turn(bot_email, channel, topic):
        return pool_resolution

    app = FastAPI()
    app.include_router(
        build_webhook_router(
            resolve_agent_by_email=resolve,
            make_agent_client=lambda email, key: FakeClient(),
            enqueue_job=enqueue,
            write_event=write_event,
            resolve_pool_bot_turn=resolve_pool_bot_turn,
        )
    )
    return app


def _payload(bot_email, token, *, message_id=1):
    return {
        "token": token,
        "bot_email": bot_email,
        "message": {
            "id": message_id,
            "content": "@**echo** hi",
            "display_recipient": "sandbox",
            "subject": "ops",
            "sender_email": "human@people.com",
            "type": "stream",
        },
    }


async def _post(app, body):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post("/zulip/incoming", json=body)


async def test_agent_valid_token_enqueues():
    agent = _resolved("echo@x", token="tok123")
    enqueued = []
    app = _build({"echo@x": agent}, enqueued)
    resp = await _post(app, _payload("echo@x", "tok123"))
    assert resp.status_code == 200
    assert len(enqueued) == 1


async def test_agent_wrong_token_rejected():
    agent = _resolved("echo@x", token="tok123")
    enqueued = []
    app = _build({"echo@x": agent}, enqueued)
    resp = await _post(app, _payload("echo@x", "WRONG"))
    assert resp.status_code == 403
    assert enqueued == []


async def test_agent_blank_secret_rejects_blank_token():
    # The Vuln 1 amplifier: an auto-provisioned agent has zulip_outgoing_token="".
    # A forged webhook sending token="" must NOT authenticate.
    agent = _resolved("echo@x", token="")
    enqueued = []
    app = _build({"echo@x": agent}, enqueued)
    resp = await _post(app, _payload("echo@x", ""))
    assert resp.status_code == 403
    assert enqueued == []


async def test_pool_valid_token_enqueues():
    pool_agent = _resolved("pool-1@x", token="unused-here")
    resolution = FakePoolResolution(
        outgoing_token="pooltok", agent=pool_agent, session_id=uuid.uuid4()
    )
    enqueued = []
    # No AgentRow for the pool bot, so resolve() returns None and the pool path runs.
    app = _build({}, enqueued, pool_resolution=resolution)
    resp = await _post(app, _payload("pool-1@x", "pooltok"))
    assert resp.status_code == 200
    assert len(enqueued) == 1


async def test_pool_wrong_token_rejected():
    pool_agent = _resolved("pool-1@x", token="unused-here")
    resolution = FakePoolResolution(
        outgoing_token="pooltok", agent=pool_agent, session_id=uuid.uuid4()
    )
    enqueued = []
    app = _build({}, enqueued, pool_resolution=resolution)
    resp = await _post(app, _payload("pool-1@x", "WRONG"))
    assert resp.status_code == 403
    assert enqueued == []


async def test_pool_blank_secret_rejects_blank_token():
    pool_agent = _resolved("pool-1@x", token="unused-here")
    resolution = FakePoolResolution(
        outgoing_token="", agent=pool_agent, session_id=uuid.uuid4()
    )
    enqueued = []
    app = _build({}, enqueued, pool_resolution=resolution)
    resp = await _post(app, _payload("pool-1@x", ""))
    assert resp.status_code == 403
    assert enqueued == []
