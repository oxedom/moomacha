import uuid
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from control_plane.db.engine import build_session_factory, create_all
from control_plane.routes.artifacts import build_artifacts_router
from control_plane.services.artifact_store import ArtifactStore


def _now():
    return datetime(2026, 5, 26, 12, 0, tzinfo=UTC)


class _FakeAgentClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, channel, topic, content):
        self.sent.append((channel, topic, content))
        return 555


class _FakeLLM:
    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        class _M:
            content = "Approved deployment"

        class _C:
            message = _M()

        class _R:
            choices = [_C()]

        return _R()


async def _build():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    events = []

    async def write_event(**kwargs):
        events.append(kwargs)

    store = ArtifactStore(factory, write_event, clock=_now)
    agent_client = _FakeAgentClient()
    enqueued = []

    async def enqueue_turn(*, agent_id, channel, topic, content, source):
        enqueued.append((agent_id, channel, topic, content, source))

    async def resolve_agent(agent_id):
        class _A:
            id = agent_id
            name = "Claw"
            zulip_bot_email = "claw@x"
            zulip_api_key = "k"

        return _A()

    app = FastAPI()
    app.include_router(
        build_artifacts_router(
            store=store,
            resolve_agent=resolve_agent,
            make_agent_client=lambda email, key: agent_client,
            enqueue_turn=enqueue_turn,
            llm_client_factory=lambda: _FakeLLM(),
            summary_model="gpt-4o-mini",
            max_payload_bytes=65536,
            base_url="https://app.test",
            clock=_now,
        )
    )
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    return client, store, agent_client, enqueued, events, engine


async def test_get_html_with_valid_token_serves_injected_document():
    client, store, _ac, _enq, _ev, engine = await _build()
    try:
        created = await store.create(
            title="Approve", html_body="<html><head></head><body>form</body></html>",
            creator_agent_id=uuid.uuid4(), source_channel="sandbox", source_topic="t",
            source_message_id=1, expires_at=_now() + timedelta(days=2),
        )
        r = await client.get(f"/ui/artifacts/{created.row.id}?token={created.raw_token}")
        assert r.status_code == 200
        assert "window.__AGENT_UI__" in r.text
        assert "Content-Security-Policy" in r.headers
    finally:
        await client.aclose()
        await engine.dispose()


async def test_get_html_bad_token_is_404():
    client, store, _ac, _enq, _ev, engine = await _build()
    try:
        created = await store.create(
            title="A", html_body="<html></html>", creator_agent_id=uuid.uuid4(),
            source_channel="c", source_topic="t", source_message_id=None,
            expires_at=_now() + timedelta(days=2),
        )
        r = await client.get(f"/ui/artifacts/{created.row.id}?token=wrong")
        assert r.status_code == 404
    finally:
        await client.aclose()
        await engine.dispose()


async def test_submit_accepts_posts_summary_and_enqueues_turn():
    client, store, agent_client, enqueued, _ev, engine = await _build()
    try:
        created = await store.create(
            title="Deploy approval", html_body="<html></html>",
            creator_agent_id=uuid.uuid4(), source_channel="sandbox", source_topic="t",
            source_message_id=1, expires_at=_now() + timedelta(days=2),
        )
        r = await client.post(
            f"/ui/artifacts/{created.row.id}/submit?token={created.raw_token}",
            json={"submission_id": "sub-1", "payload": {"approved": True}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "accepted"
        assert len(agent_client.sent) == 1
        assert len(enqueued) == 1
    finally:
        await client.aclose()
        await engine.dispose()


async def test_submit_duplicate_submission_id_no_repost_no_reenqueue():
    client, store, agent_client, enqueued, _ev, engine = await _build()
    try:
        created = await store.create(
            title="A", html_body="<html></html>", creator_agent_id=uuid.uuid4(),
            source_channel="sandbox", source_topic="t", source_message_id=1,
            expires_at=_now() + timedelta(days=2),
        )
        url = f"/ui/artifacts/{created.row.id}/submit?token={created.raw_token}"
        body = {"submission_id": "sub-1", "payload": {"a": 1}}
        await client.post(url, json=body)
        r2 = await client.post(url, json=body)
        assert r2.status_code == 200
        assert r2.json()["outcome"] == "duplicate"
        assert len(agent_client.sent) == 1
        assert len(enqueued) == 1
    finally:
        await client.aclose()
        await engine.dispose()


async def test_submit_competing_submission_id_returns_409():
    client, store, _ac, _enq, _ev, engine = await _build()
    try:
        created = await store.create(
            title="A", html_body="<html></html>", creator_agent_id=uuid.uuid4(),
            source_channel="sandbox", source_topic="t", source_message_id=1,
            expires_at=_now() + timedelta(days=2),
        )
        url = f"/ui/artifacts/{created.row.id}/submit?token={created.raw_token}"
        await client.post(url, json={"submission_id": "sub-1", "payload": {"a": 1}})
        r2 = await client.post(url, json={"submission_id": "sub-2", "payload": {"a": 2}})
        assert r2.status_code == 409
    finally:
        await client.aclose()
        await engine.dispose()


async def test_payload_endpoint_returns_full_payload_with_token():
    client, store, _ac, _enq, _ev, engine = await _build()
    try:
        created = await store.create(
            title="A", html_body="<html></html>", creator_agent_id=uuid.uuid4(),
            source_channel="sandbox", source_topic="t", source_message_id=1,
            expires_at=_now() + timedelta(days=2),
        )
        url = f"/ui/artifacts/{created.row.id}/submit?token={created.raw_token}"
        await client.post(url, json={"submission_id": "sub-1", "payload": {"approved": True}})
        r = await client.get(f"/ui/artifacts/{created.row.id}/payload?token={created.raw_token}")
        assert r.status_code == 200
        assert r.json()["payload"] == {"approved": True}
    finally:
        await client.aclose()
        await engine.dispose()


async def test_get_html_sets_referrer_and_cache_headers_to_protect_token():
    client, store, _ac, _enq, _ev, engine = await _build()
    try:
        created = await store.create(
            title="A", html_body="<html><head></head><body>x</body></html>",
            creator_agent_id=uuid.uuid4(), source_channel="sandbox", source_topic="t",
            source_message_id=1, expires_at=_now() + timedelta(days=2),
        )
        r = await client.get(f"/ui/artifacts/{created.row.id}?token={created.raw_token}")
        assert r.status_code == 200
        # The token lives in the URL; it must not leak to CDNs via Referer, and
        # must not be stored by shared caches.
        assert r.headers.get("Referrer-Policy") == "no-referrer"
        assert "no-store" in r.headers.get("Cache-Control", "")
    finally:
        await client.aclose()
        await engine.dispose()
