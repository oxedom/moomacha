"""Persistence for generated media artifacts."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import GeneratedMediaArtifactRow

WriteEvent = Callable[..., Awaitable[None]]


def _now_utc() -> datetime:
    return datetime.now(UTC)


class GeneratedMediaStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        write_event: WriteEvent,
        clock: Callable[[], datetime] = _now_utc,
    ) -> None:
        self._factory = session_factory
        self._write_event = write_event
        self._clock = clock

    async def create(
        self,
        *,
        creator_agent_id: uuid.UUID,
        source_channel: str,
        source_topic: str,
        source_message_id: int | None,
        conversation_type: str,
        prompt: str,
        revised_prompt: str | None,
        model: str,
        params: dict,
        mime_type: str,
        filename: str,
        data: bytes,
        artifact_id: uuid.UUID | None = None,
        storage_backend: str = "postgres_binary",
        storage_ref: str | None = None,
    ) -> GeneratedMediaArtifactRow:
        row = GeneratedMediaArtifactRow(
            id=artifact_id or uuid.uuid4(),
            creator_agent_id=creator_agent_id,
            source_channel=source_channel,
            source_topic=source_topic,
            source_message_id=source_message_id,
            conversation_type=conversation_type,
            prompt=prompt,
            revised_prompt=revised_prompt,
            model=model,
            params=params,
            mime_type=mime_type,
            filename=filename,
            sha256=hashlib.sha256(data).hexdigest(),
            byte_length=len(data),
            data=data,
            storage_backend=storage_backend,
            storage_ref=storage_ref,
            created_at=self._clock(),
        )
        async with self._factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            session.expunge(row)
        await self._emit("generated_media_created", row)
        return row

    async def mark_posted(
        self,
        artifact_id: uuid.UUID,
        *,
        zulip_upload_url: str,
        zulip_message_id: int,
    ) -> GeneratedMediaArtifactRow:
        async with self._factory() as session:
            row = await session.get(GeneratedMediaArtifactRow, artifact_id)
            if row is None:
                raise KeyError(f"generated media artifact {artifact_id} not found")
            row.zulip_upload_url = zulip_upload_url
            row.zulip_message_id = zulip_message_id
            await session.commit()
            await session.refresh(row)
            session.expunge(row)
        await self._emit("generated_media_posted", row)
        return row

    async def _emit(self, event_type: str, row: GeneratedMediaArtifactRow) -> None:
        await self._write_event(
            actor_type="agent",
            event_type=event_type,
            payload={
                "artifact_id": str(row.id),
                "filename": row.filename,
                "mime_type": row.mime_type,
                "sha256": row.sha256,
                "byte_length": row.byte_length,
                "model": row.model,
                "zulip_upload_url": row.zulip_upload_url,
                "zulip_message_id": row.zulip_message_id,
            },
            actor_id=row.creator_agent_id,
            related_agent_id=row.creator_agent_id,
            related_channel=row.source_channel,
            source_message_id=row.source_message_id,
        )
