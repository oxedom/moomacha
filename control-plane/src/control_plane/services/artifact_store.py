"""Persistence + lifecycle for interactive response artifacts.

Mirrors ScheduleStore: a session_factory and a write_event callable are injected,
and the store emits its own events (callers MUST NOT re-emit them). The artifact
token is a bearer capability: the raw token is returned exactly once from create()
and never persisted — only its sha256 hash is stored and compared.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import InteractiveArtifactRow, InteractiveSubmissionRow

logger = logging.getLogger("control_plane")

WriteEvent = Callable[..., Awaitable[None]]


def _now_utc() -> datetime:
    return datetime.now(UTC)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class CreatedArtifact:
    row: InteractiveArtifactRow
    raw_token: str  # returned once; never stored


@dataclass
class SubmissionResult:
    outcome: str  # accepted | duplicate | conflict | expired | revoked
    submission: InteractiveSubmissionRow | None = None


class ArtifactStore:
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
        title: str,
        html_body: str,
        creator_agent_id: uuid.UUID,
        source_channel: str,
        source_topic: str,
        source_message_id: int | None,
        expires_at: datetime,
        conversation_type: str = "stream",
    ) -> CreatedArtifact:
        raw_token = secrets.token_urlsafe(32)
        row = InteractiveArtifactRow(
            title=title,
            html_body=html_body,
            creator_agent_id=creator_agent_id,
            source_channel=source_channel,
            source_topic=source_topic,
            source_message_id=source_message_id,
            conversation_type=conversation_type,
            token_hash=hash_token(raw_token),
            status="open",
            expires_at=expires_at,
        )
        async with self._factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        await self._emit("interactive_artifact_created", row)
        return CreatedArtifact(row=row, raw_token=raw_token)

    async def get_verified(
        self, artifact_id: uuid.UUID, raw_token: str
    ) -> InteractiveArtifactRow | None:
        """Fetch an artifact only if the bearer token matches. Returns a detached row.

        Does NOT enforce expiry/status — callers decide what to do with an expired
        or submitted artifact (read paths still serve a receipt)."""
        async with self._factory() as session:
            row = await session.get(InteractiveArtifactRow, artifact_id)
            if row is None:
                return None
            if not hmac.compare_digest(row.token_hash, hash_token(raw_token)):
                return None
            session.expunge(row)
            return row

    async def expire_if_due(self, row: InteractiveArtifactRow) -> bool:
        """If an OPEN artifact is past its expiry, flip it to 'expired'. Returns True
        if it changed. Idempotent: only acts on status=='open'. Mutates `row` in place
        so the caller's detached object reflects the new status."""
        if row.status != "open" or row.expires_at > self._clock():
            return False
        async with self._factory() as session:
            db_row = await session.get(InteractiveArtifactRow, row.id)
            if db_row is None or db_row.status != "open":
                row.status = db_row.status if db_row else row.status
                return False
            db_row.status = "expired"
            await session.commit()
            await session.refresh(db_row)
            session.expunge(db_row)
        row.status = "expired"
        await self._emit("interactive_artifact_expired", row)
        return True

    async def accept_submission(
        self,
        *,
        artifact_id: uuid.UUID,
        submission_id: str,
        payload: dict,
        summary_text: str,
        summary_model: str | None,
        summary_status: str,
    ) -> SubmissionResult:
        """Atomically record the one accepted submission for an artifact.

        - accepted: inserted and artifact flipped open->submitted (emits event).
        - duplicate: the same submission_id already won; returns the existing row,
          emits nothing (idempotent replay of a double-click/retry).
        - conflict: a DIFFERENT submission_id already won; returns the winner.
        - expired/revoked: artifact is not open; nothing recorded.
        """
        async with self._factory() as session:
            art = await session.get(InteractiveArtifactRow, artifact_id)
            if art is None:
                return SubmissionResult(outcome="revoked")
            if art.status == "open" and art.expires_at <= self._clock():
                art.status = "expired"
                await session.commit()
                await self._emit_expired(art)
                return SubmissionResult(outcome="expired")
            if art.status != "open":
                existing = await self._existing_submission(session, artifact_id)
                if existing is not None and existing.submission_id == submission_id:
                    return SubmissionResult(outcome="duplicate", submission=existing)
                if art.status in ("expired", "revoked"):
                    return SubmissionResult(outcome=art.status, submission=existing)
                return SubmissionResult(outcome="conflict", submission=existing)
            sub = InteractiveSubmissionRow(
                artifact_id=artifact_id, submission_id=submission_id,
                payload_full=payload, summary_text=summary_text,
                summary_model=summary_model, summary_status=summary_status,
            )
            session.add(sub)
            art.status = "submitted"
            art.submitted_at = self._clock()
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await self._existing_submission(session, artifact_id)
                if existing is not None and existing.submission_id == submission_id:
                    return SubmissionResult(outcome="duplicate", submission=existing)
                return SubmissionResult(outcome="conflict", submission=existing)
            await session.refresh(sub)
            await session.refresh(art)
            session.expunge(sub)
        await self._emit("interactive_submission_received", art)
        return SubmissionResult(outcome="accepted", submission=sub)

    async def existing_submission_for_api(
        self, artifact_id: uuid.UUID
    ) -> InteractiveSubmissionRow | None:
        async with self._factory() as session:
            return await self._existing_submission(session, artifact_id)

    async def _existing_submission(
        self, session: AsyncSession, artifact_id: uuid.UUID
    ) -> InteractiveSubmissionRow | None:
        row = (
            await session.execute(
                select(InteractiveSubmissionRow).where(
                    InteractiveSubmissionRow.artifact_id == artifact_id
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            session.expunge(row)
        return row

    async def _emit_expired(self, art: InteractiveArtifactRow) -> None:
        await self._emit("interactive_artifact_expired", art)

    async def _emit(self, event_type: str, row: InteractiveArtifactRow) -> None:
        await self._write_event(
            actor_type="agent",
            event_type=event_type,
            payload={"artifact_id": str(row.id), "status": row.status},
            actor_id=row.creator_agent_id,
            related_agent_id=row.creator_agent_id,
            related_channel=row.source_channel,
            source_message_id=row.source_message_id,
        )
