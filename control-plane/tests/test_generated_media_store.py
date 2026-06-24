import hashlib
import uuid
from datetime import UTC, datetime

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import GeneratedMediaArtifactRow
from control_plane.services.generated_media_store import GeneratedMediaStore


def _now():
    return datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


async def _store():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    events = []

    async def fake_write_event(**kwargs):
        events.append(kwargs)

    return GeneratedMediaStore(factory, fake_write_event, clock=_now), factory, engine, events


async def test_create_computes_hash_and_emits_event():
    store, _factory, engine, events = await _store()
    try:
        data = b"image-bytes"
        row = await store.create(
            creator_agent_id=uuid.uuid4(),
            source_channel="sandbox",
            source_topic="images",
            source_message_id=10,
            conversation_type="stream",
            prompt="draw",
            revised_prompt=None,
            model="gpt-image-2",
            params={"size": "1024x1024"},
            mime_type="image/png",
            filename="claw-test.png",
            data=data,
        )
        assert row.sha256 == hashlib.sha256(data).hexdigest()
        assert row.byte_length == len(data)
        assert row.data == data
        assert row.created_at == _now()
        assert any(e["event_type"] == "generated_media_created" for e in events)
    finally:
        await engine.dispose()


async def test_mark_posted_updates_row_and_emits_event():
    store, factory, engine, events = await _store()
    try:
        row = await store.create(
            creator_agent_id=uuid.uuid4(),
            source_channel="sandbox",
            source_topic="images",
            source_message_id=None,
            conversation_type="stream",
            prompt="draw",
            revised_prompt="draw revised",
            model="gpt-image-2",
            params={},
            mime_type="image/png",
            filename="claw-test.png",
            data=b"x",
        )
        posted = await store.mark_posted(
            row.id,
            zulip_upload_url="/user_uploads/1/a/claw-test.png",
            zulip_message_id=999,
        )
        assert posted.zulip_upload_url == "/user_uploads/1/a/claw-test.png"
        assert posted.zulip_message_id == 999
        async with factory() as session:
            reread = await session.get(GeneratedMediaArtifactRow, row.id)
            assert reread.zulip_message_id == 999
        assert any(e["event_type"] == "generated_media_posted" for e in events)
    finally:
        await engine.dispose()
