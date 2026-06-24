import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.routes.zulip_webhook import build_webhook_router
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.job_queue import Job
from control_plane.services.pool_resolver import PoolBotNoSession, PoolBotTurnResult


def _pool_agent(session_id: uuid.UUID) -> ResolvedAgent:
    return ResolvedAgent(
        id=uuid.uuid4(),
        name="Scout",
        persona="You help with tasks.",
        model_id="gpt-4o",
        zulip_bot_id=77,
        zulip_bot_email="worker1@example.com",
        zulip_api_key="bot-key",
        zulip_outgoing_token="pool-tok",
        context_message_count=20,
        readable_channels=["sandbox"],
        runtime_kind="deepagents",
    )


def _build(enqueued, reactions, pool_resolver):
    async def resolve_agent(email):
        return None  # pool bot — not in AgentRow

    class FakeClient:
        def __init__(self, email, key):
            self._email = email

        async def add_reaction(self, mid, emoji):
            reactions.append((mid, emoji, self._email))

    async def enqueue(job):
        enqueued.append(job)

    async def write_event(**kw):
        return None

    app = FastAPI()
    app.include_router(
        build_webhook_router(
            resolve_agent_by_email=resolve_agent,
            make_agent_client=lambda e, k: FakeClient(e, k),
            enqueue_job=enqueue,
            write_event=write_event,
            resolve_pool_bot_turn=pool_resolver,
        )
    )
    return TestClient(app)


def _stream_payload(bot_email="worker1@example.com", token="pool-tok"):
    return {
        "token": token,
        "bot_email": bot_email,
        "trigger": "mention",
        "message": {
            "id": 200,
            "content": "@**Scout** help me",
            "display_recipient": "sandbox",
            "subject": "task-1",
            "type": "stream",
            "sender_email": "alice@example.com",
        },
    }


def test_pool_bot_mention_enqueues_session_job():
    session_id = uuid.uuid4()
    enqueued, reactions = [], []

    async def pool_resolver(email, channel, topic):
        if email == "worker1@example.com":
            return PoolBotTurnResult(
                outgoing_token="pool-tok",
                agent=_pool_agent(session_id),
                session_id=session_id,
            )
        return None

    client = _build(enqueued, reactions, pool_resolver)
    resp = client.post("/zulip/incoming", json=_stream_payload())

    assert resp.status_code == 200
    assert len(enqueued) == 1
    job: Job = enqueued[0]
    assert job.session_id == session_id
    assert job.channel == "sandbox"
    assert job.topic == "task-1"
    assert reactions == [(200, "+1", "worker1@example.com")]


def test_pool_bot_wrong_token_returns_403():
    session_id = uuid.uuid4()

    async def pool_resolver(email, channel, topic):
        return PoolBotTurnResult(
            outgoing_token="correct-tok",
            agent=_pool_agent(session_id),
            session_id=session_id,
        )

    client = _build([], [], pool_resolver)
    resp = client.post("/zulip/incoming", json=_stream_payload(token="wrong-tok"))
    assert resp.status_code == 403


def test_pool_bot_no_session_returns_200_silently():
    enqueued = []

    async def pool_resolver(email, channel, topic):
        return PoolBotNoSession(outgoing_token="pool-tok")

    client = _build(enqueued, [], pool_resolver)
    resp = client.post("/zulip/incoming", json=_stream_payload())
    assert resp.status_code == 200
    assert len(enqueued) == 0


def test_unknown_email_falls_through_to_unknown_bot():
    enqueued = []

    async def pool_resolver(email, channel, topic):
        return None  # not a pool bot

    client = _build(enqueued, [], pool_resolver)
    resp = client.post("/zulip/incoming", json=_stream_payload(bot_email="totally-unknown@x"))
    assert resp.status_code == 200
    assert len(enqueued) == 0


def test_no_pool_resolver_injected_falls_through():
    """When resolve_pool_bot_turn is None (default), pool bots are treated as unknown."""
    enqueued = []

    async def resolve_agent(email):
        return None

    async def enqueue(job):
        enqueued.append(job)

    async def write_event(**kw):
        return None

    app = FastAPI()
    app.include_router(
        build_webhook_router(
            resolve_agent_by_email=resolve_agent,
            make_agent_client=lambda e, k: None,
            enqueue_job=enqueue,
            write_event=write_event,
            # no resolve_pool_bot_turn
        )
    )
    client = TestClient(app)
    resp = client.post("/zulip/incoming", json=_stream_payload())
    assert resp.status_code == 200
    assert len(enqueued) == 0
