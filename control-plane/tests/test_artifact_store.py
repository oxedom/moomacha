import uuid
from datetime import UTC, datetime, timedelta

from control_plane.db.engine import build_session_factory, create_all
from control_plane.services.artifact_store import ArtifactStore


def _now():
    return datetime(2026, 5, 26, 12, 0, tzinfo=UTC)


async def _store():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    events = []

    async def fake_write_event(**kwargs):
        events.append(kwargs)

    return ArtifactStore(factory, fake_write_event, clock=_now), factory, engine, events


async def test_create_returns_raw_token_and_stores_only_hash_and_emits_event():
    store, _factory, engine, events = await _store()
    try:
        created = await store.create(
            title="Approve",
            html_body="<!doctype html><p>x</p>",
            creator_agent_id=uuid.uuid4(),
            source_channel="sandbox",
            source_topic="t",
            source_message_id=42,
            expires_at=_now() + timedelta(days=2),
        )
        assert created.raw_token  # returned exactly once
        assert created.row.token_hash != created.raw_token
        assert created.row.status == "open"
        assert any(e["event_type"] == "interactive_artifact_created" for e in events)
    finally:
        await engine.dispose()


async def test_get_with_valid_token_returns_row_and_bad_token_returns_none():
    store, _factory, engine, _events = await _store()
    try:
        created = await store.create(
            title="A", html_body="<p>x</p>", creator_agent_id=uuid.uuid4(),
            source_channel="c", source_topic="t", source_message_id=None,
            expires_at=_now() + timedelta(days=2),
        )
        ok = await store.get_verified(created.row.id, created.raw_token)
        assert ok is not None and ok.id == created.row.id
        bad = await store.get_verified(created.row.id, "not-the-token")
        assert bad is None
        missing = await store.get_verified(uuid.uuid4(), created.raw_token)
        assert missing is None
    finally:
        await engine.dispose()


async def test_expire_if_due_flips_open_to_expired_and_emits():
    store, factory, engine, events = await _store()
    try:
        created = await store.create(
            title="A", html_body="<p>x</p>", creator_agent_id=uuid.uuid4(),
            source_channel="c", source_topic="t", source_message_id=None,
            expires_at=_now() - timedelta(minutes=1),  # already past
        )
        row = await store.get_verified(created.row.id, created.raw_token)
        changed = await store.expire_if_due(row)
        assert changed is True
        again = await store.get_verified(created.row.id, created.raw_token)
        assert again.status == "expired"
        assert any(e["event_type"] == "interactive_artifact_expired" for e in events)
        # Idempotent: a second call on an already-expired row does nothing.
        assert await store.expire_if_due(again) is False
    finally:
        await engine.dispose()


async def test_expire_if_due_noop_when_not_due():
    store, _factory, engine, _events = await _store()
    try:
        created = await store.create(
            title="A", html_body="<p>x</p>", creator_agent_id=uuid.uuid4(),
            source_channel="c", source_topic="t", source_message_id=None,
            expires_at=_now() + timedelta(days=1),
        )
        row = await store.get_verified(created.row.id, created.raw_token)
        assert await store.expire_if_due(row) is False
    finally:
        await engine.dispose()


async def _open_artifact(store):
    return await store.create(
        title="A", html_body="<p>x</p>", creator_agent_id=uuid.uuid4(),
        source_channel="c", source_topic="t", source_message_id=7,
        expires_at=_now() + timedelta(days=2),
    )


async def test_accept_submission_first_time_accepts_and_marks_submitted():
    store, _factory, engine, events = await _store()
    try:
        created = await _open_artifact(store)
        res = await store.accept_submission(
            artifact_id=created.row.id, submission_id="sub-1",
            payload={"approved": True}, summary_text="Approved",
            summary_model="gpt-4o-mini", summary_status="generated",
        )
        assert res.outcome == "accepted"
        assert res.submission.submission_id == "sub-1"
        reread = await store.get_verified(created.row.id, created.raw_token)
        assert reread.status == "submitted" and reread.submitted_at is not None
        assert any(e["event_type"] == "interactive_submission_received" for e in events)
    finally:
        await engine.dispose()


async def test_accept_submission_same_submission_id_is_idempotent_duplicate():
    store, _factory, engine, events = await _store()
    try:
        created = await _open_artifact(store)
        await store.accept_submission(
            artifact_id=created.row.id, submission_id="sub-1", payload={"a": 1},
            summary_text="s", summary_model="m", summary_status="generated",
        )
        res = await store.accept_submission(
            artifact_id=created.row.id, submission_id="sub-1", payload={"a": 1},
            summary_text="s", summary_model="m", summary_status="generated",
        )
        assert res.outcome == "duplicate"
        assert res.submission.submission_id == "sub-1"
        assert sum(e["event_type"] == "interactive_submission_received" for e in events) == 1
    finally:
        await engine.dispose()


async def test_accept_submission_competing_submission_id_conflicts():
    store, _factory, engine, _events = await _store()
    try:
        created = await _open_artifact(store)
        await store.accept_submission(
            artifact_id=created.row.id, submission_id="sub-1", payload={"a": 1},
            summary_text="s", summary_model="m", summary_status="generated",
        )
        res = await store.accept_submission(
            artifact_id=created.row.id, submission_id="sub-2", payload={"a": 2},
            summary_text="s", summary_model="m", summary_status="generated",
        )
        assert res.outcome == "conflict"
        assert res.submission.submission_id == "sub-1"  # the winner
    finally:
        await engine.dispose()


async def test_accept_submission_on_expired_artifact_rejected():
    store, _factory, engine, _events = await _store()
    try:
        created = await store.create(
            title="A", html_body="<p>x</p>", creator_agent_id=uuid.uuid4(),
            source_channel="c", source_topic="t", source_message_id=None,
            expires_at=_now() - timedelta(minutes=1),
        )
        res = await store.accept_submission(
            artifact_id=created.row.id, submission_id="sub-1", payload={"a": 1},
            summary_text="s", summary_model="m", summary_status="generated",
        )
        assert res.outcome == "expired"
        assert res.submission is None
    finally:
        await engine.dispose()
