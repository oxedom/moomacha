import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import InteractiveArtifactRow, InteractiveSubmissionRow


async def _factory():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    return factory, engine


async def test_artifact_row_persists_with_defaults():
    factory, engine = await _factory()
    try:
        art = InteractiveArtifactRow(
            title="Deploy approval",
            html_body="<!doctype html><p>hi</p>",
            creator_agent_id=uuid.uuid4(),
            source_channel="sandbox",
            source_topic="t",
            token_hash="abc",
            status="open",
            expires_at=datetime.now(UTC) + timedelta(days=2),
        )
        async with factory() as s:
            s.add(art)
            await s.commit()
            await s.refresh(art)
        assert art.conversation_type == "stream"
        assert art.storage_backend == "postgres_text"
        assert art.submitted_at is None
        assert art.created_at is not None and art.updated_at is not None
    finally:
        await engine.dispose()


async def test_one_submission_per_artifact_enforced():
    factory, engine = await _factory()
    try:
        art_id = uuid.uuid4()
        async with factory() as s:
            s.add_all([
                InteractiveSubmissionRow(
                    artifact_id=art_id, submission_id="a", payload_full={"x": 1},
                    summary_text="s", summary_status="generated",
                ),
                InteractiveSubmissionRow(
                    artifact_id=art_id, submission_id="b", payload_full={"x": 2},
                    summary_text="s", summary_status="generated",
                ),
            ])
            with pytest.raises(IntegrityError):
                await s.commit()
    finally:
        await engine.dispose()
