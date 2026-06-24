import uuid

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import GeneratedMediaArtifactRow


async def _factory():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    return factory, engine


async def test_generated_media_artifact_row_persists_with_defaults():
    factory, engine = await _factory()
    try:
        row = GeneratedMediaArtifactRow(
            creator_agent_id=uuid.uuid4(),
            source_channel="sandbox",
            source_topic="images",
            source_message_id=42,
            prompt="draw a test image",
            model="gpt-image-2",
            params={"size": "1024x1024"},
            mime_type="image/png",
            filename="claw-test.png",
            sha256="0" * 64,
            byte_length=4,
            data=b"test",
        )
        async with factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        assert row.conversation_type == "stream"
        assert row.storage_backend == "postgres_binary"
        assert row.storage_ref is None
        assert row.zulip_upload_url is None
        assert row.zulip_message_id is None
        assert row.created_at is not None
    finally:
        await engine.dispose()
