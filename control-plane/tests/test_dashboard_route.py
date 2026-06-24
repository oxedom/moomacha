import uuid
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import AgentRow, EventRow
from control_plane.routes.dashboard import build_dashboard_router


def _agent(
    *,
    agent_id: uuid.UUID,
    name: str,
    enabled: bool = True,
    channels: list[str] | None = None,
) -> AgentRow:
    return AgentRow(
        id=agent_id,
        name=name,
        persona="p",
        model_id="gpt-4o",
        zulip_bot_id=100,
        zulip_bot_email=f"{name.lower()}@example.test",
        zulip_api_key_encrypted="encrypted-key",
        zulip_outgoing_token_encrypted="encrypted-token",
        context_message_count=20,
        readable_channels=channels or ["sandbox"],
        allowed_tools=[],
        provisioning_status="active",
        enabled=enabled,
        is_bastion=False,
    )


async def _client(now: datetime):
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    app = FastAPI()
    app.include_router(build_dashboard_router(factory, clock=lambda: now))
    client = httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )
    return client, factory, engine


async def _seed(factory, *rows) -> None:
    async with factory() as session:
        session.add_all(rows)
        await session.commit()


async def test_dashboard_snapshot_shape_counts_last_seen_and_resolution():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    echo_id = uuid.uuid4()
    idle_id = uuid.uuid4()
    client, factory, engine = await _client(now)
    try:
        await _seed(
            factory,
            _agent(agent_id=echo_id, name="Echo", enabled=True),
            _agent(agent_id=idle_id, name="Idle", enabled=False, channels=["ops"]),
            EventRow(
                timestamp=now - timedelta(minutes=10),
                actor_type="agent",
                actor_id=None,
                event_type="turn.end",
                status="ok",
                payload={},
                related_agent_id=echo_id,
                related_channel="sandbox",
                source_message_id=101,
            ),
            EventRow(
                timestamp=now - timedelta(minutes=5),
                actor_type="agent",
                actor_id=echo_id,
                event_type="error",
                payload={},
                related_agent_id=None,
                related_channel="ops",
                source_message_id=102,
            ),
            EventRow(
                timestamp=now - timedelta(minutes=30),
                actor_type="system",
                actor_id=None,
                event_type="schedule_errored",
                payload={},
                related_agent_id=idle_id,
                related_channel="sandbox",
                source_message_id=103,
            ),
            EventRow(
                timestamp=now - timedelta(hours=2),
                actor_type="agent",
                actor_id=None,
                event_type="turn.end",
                status="ok",
                payload={},
                related_agent_id=idle_id,
                related_channel="legacy",
                source_message_id=104,
            ),
        )

        response = await client.get("/dashboard/snapshot")
        assert response.status_code == 200
        body = response.json()
        assert body["generated_at"] == "2026-05-25T12:00:00Z"
        assert body["summary"] == {
            "agents_total": 2,
            "agents_enabled": 1,
            "replies_last_hour": 1,
            "errors_last_hour": 2,
            "active_channels_last_hour": 2,
        }

        agents = {agent["name"]: agent for agent in body["agents"]}
        assert agents["Echo"]["last_event_type"] == "error"
        assert agents["Echo"]["last_seen"] == "2026-05-25T11:55:00Z"
        assert agents["Idle"]["enabled"] is False
        assert agents["Idle"]["last_event_type"] == "schedule_errored"

        assert [event["event_type"] for event in body["events"]] == [
            "error",
            "turn.end",
            "schedule_errored",
            "turn.end",
        ]
        assert body["events"][0]["agent_name"] == "Echo"
        assert body["events"][0]["channel"] == "ops"
    finally:
        await client.aclose()
        await engine.dispose()


async def test_dashboard_snapshot_caps_recent_events_newest_first():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    agent_id = uuid.uuid4()
    client, factory, engine = await _client(now)
    try:
        rows = [_agent(agent_id=agent_id, name="Echo")]
        rows.extend(
            EventRow(
                timestamp=now - timedelta(seconds=index),
                actor_type="agent",
                actor_id=None,
                event_type="turn.end",
                status="ok",
                payload={},
                related_agent_id=agent_id,
                related_channel="sandbox",
                source_message_id=index,
            )
            for index in range(55)
        )
        await _seed(factory, *rows)

        body = (await client.get("/dashboard/snapshot")).json()
        assert len(body["events"]) == 50
        assert [event["source_message_id"] for event in body["events"]] == list(range(50))
    finally:
        await client.aclose()
        await engine.dispose()


async def test_dashboard_snapshot_uses_one_hour_boundary():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    agent_id = uuid.uuid4()
    client, factory, engine = await _client(now)
    try:
        await _seed(
            factory,
            _agent(agent_id=agent_id, name="Echo"),
            EventRow(
                timestamp=now - timedelta(hours=1),
                actor_type="agent",
                actor_id=None,
                event_type="turn.end",
                status="ok",
                payload={},
                related_agent_id=agent_id,
                related_channel="sandbox",
                source_message_id=201,
            ),
            EventRow(
                timestamp=now - timedelta(hours=1, seconds=1),
                actor_type="agent",
                actor_id=None,
                event_type="turn.end",
                status="ok",
                payload={},
                related_agent_id=agent_id,
                related_channel="old",
                source_message_id=202,
            ),
        )

        summary = (await client.get("/dashboard/snapshot")).json()["summary"]
        assert summary["replies_last_hour"] == 1
        assert summary["active_channels_last_hour"] == 1
    finally:
        await client.aclose()
        await engine.dispose()


async def test_dashboard_snapshot_allows_unresolved_event_agent():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    client, factory, engine = await _client(now)
    try:
        await _seed(
            factory,
            EventRow(
                timestamp=now,
                actor_type="system",
                actor_id=None,
                event_type="unknown_bot",
                payload={},
                related_agent_id=None,
                related_channel=None,
                source_message_id=None,
            ),
        )

        body = (await client.get("/dashboard/snapshot")).json()
        assert body["events"][0]["agent_name"] is None
        assert body["events"][0]["channel"] is None
    finally:
        await client.aclose()
        await engine.dispose()


async def test_dashboard_empty_system_and_page_smoke():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    client, _factory, engine = await _client(now)
    try:
        snapshot = await client.get("/dashboard/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["summary"] == {
            "agents_total": 0,
            "agents_enabled": 0,
            "replies_last_hour": 0,
            "errors_last_hour": 0,
            "active_channels_last_hour": 0,
        }
        assert snapshot.json()["agents"] == []
        assert snapshot.json()["events"] == []

        page = await client.get("/dashboard")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
        assert "Live Agent Monitor" in page.text
        assert "/dashboard/snapshot" in page.text
    finally:
        await client.aclose()
        await engine.dispose()
