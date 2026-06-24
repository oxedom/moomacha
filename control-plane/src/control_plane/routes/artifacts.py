"""Public bearer-token endpoints for interactive response artifacts.

Authority is always derived server-side from the stored artifact + token; the
browser controls only the submission payload. One URL token covers view, status,
submit, and full-payload until expiry."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from control_plane.services.artifact_html import CSP, inject_config
from control_plane.services.artifact_store import ArtifactStore
from control_plane.services.artifact_summary import summarize_payload
from control_plane.services.job_source import InteractiveSubmissionSource

logger = logging.getLogger("control_plane")


class SubmitBody(BaseModel):
    submission_id: str = Field(min_length=1, max_length=255)
    payload: Any = None
    # artifact_id and any authority fields the client sends are ignored.


def build_artifacts_router(
    *,
    store: ArtifactStore,
    resolve_agent: Callable[[uuid.UUID], Awaitable[Any]],
    make_agent_client: Callable[[str, str], Any],
    enqueue_turn: Callable[..., Awaitable[None]],
    llm_client_factory: Callable[[], Any],
    summary_model: str,
    max_payload_bytes: int,
    base_url: str,
    clock: Callable[[], datetime],
) -> APIRouter:
    router = APIRouter(prefix="/ui/artifacts", tags=["artifacts"])

    def _token(request: Request) -> str:
        return request.query_params.get("token", "")

    async def _load(artifact_id: uuid.UUID, token: str):
        """Return the row for a valid token, applying lazy expiry; else None."""
        row = await store.get_verified(artifact_id, token)
        if row is None:
            return None
        await store.expire_if_due(row)  # mutates row.status in place
        return row

    @router.get("/{artifact_id}")
    async def get_html(artifact_id: uuid.UUID, request: Request) -> Response:
        row = await _load(artifact_id, _token(request))
        if row is None:
            return HTMLResponse("Not found", status_code=404)
        if row.status in ("expired", "revoked"):
            return HTMLResponse("This interactive response has expired.", status_code=410)
        token = _token(request)
        cfg = {
            "artifactId": str(row.id),
            "submissionId": str(uuid.uuid4()),  # default idempotency key for AgentUI.submit
            "status": row.status,
            "submitUrl": f"{base_url}/ui/artifacts/{row.id}/submit?token={token}",
            "statusUrl": f"{base_url}/ui/artifacts/{row.id}/status?token={token}",
            "fullPayloadUrl": f"{base_url}/ui/artifacts/{row.id}/payload?token={token}",
        }
        html = inject_config(row.html_body, cfg)
        return HTMLResponse(
            html,
            headers={
                "Content-Security-Policy": CSP,
                # The bearer token is in the query string: prevent it leaking to the
                # CDNs the artifact loads (via Referer) and being stored by shared caches.
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            },
        )

    @router.get("/{artifact_id}/status")
    async def get_status(artifact_id: uuid.UUID, request: Request) -> Response:
        row = await _load(artifact_id, _token(request))
        if row is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse({
            "artifact_id": str(row.id),
            "status": row.status,
            "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
            "expires_at": row.expires_at.isoformat(),
        })

    @router.get("/{artifact_id}/payload")
    async def get_payload(artifact_id: uuid.UUID, request: Request) -> Response:
        row = await _load(artifact_id, _token(request))
        if row is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if row.status in ("expired", "revoked"):
            return JSONResponse({"error": "expired"}, status_code=410)
        sub = await store.existing_submission_for_api(artifact_id)
        if sub is None:
            return JSONResponse({"error": "no_submission"}, status_code=404)
        return JSONResponse({
            "artifact_id": str(artifact_id),
            "submission_id": sub.submission_id,
            "payload": sub.payload_full,
            "summary": sub.summary_text,
        })

    @router.post("/{artifact_id}/submit")
    async def submit(artifact_id: uuid.UUID, request: Request) -> Response:
        token = _token(request)
        row = await _load(artifact_id, token)
        if row is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if row.status == "expired":
            return JSONResponse({"error": "expired"}, status_code=410)
        if row.status == "revoked":
            return JSONResponse({"error": "revoked"}, status_code=410)

        raw = await request.body()
        if len(raw) > max_payload_bytes:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            body = SubmitBody.model_validate_json(raw)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid_body"}, status_code=400)

        # Summarize first (wasted only on the rare race loser); accept_submission is
        # the authority and the race guard.
        llm = llm_client_factory()
        try:
            summary_text, summary_model_used, summary_status = await summarize_payload(
                llm, model=summary_model, title=row.title, payload=body.payload,
            )
        finally:
            close = getattr(llm, "close", None)
            if close is not None:
                await close()

        result = await store.accept_submission(
            artifact_id=artifact_id,
            submission_id=body.submission_id,
            payload=body.payload if isinstance(body.payload, dict) else {"value": body.payload},
            summary_text=summary_text,
            summary_model=summary_model_used,
            summary_status=summary_status,
        )

        if result.outcome == "conflict":
            return JSONResponse({"error": "already_submitted"}, status_code=409)
        if result.outcome in ("expired", "revoked"):
            return JSONResponse({"error": result.outcome}, status_code=410)
        if result.outcome == "duplicate":
            sub = result.submission
            return JSONResponse({
                "outcome": "duplicate",
                "summary": sub.summary_text if sub else summary_text,
            })

        # outcome == accepted: post visible summary + resume the creator agent.
        payload_url = f"{base_url}/ui/artifacts/{artifact_id}/payload?token={token}"
        message = (
            f"Interactive response submitted: {summary_text}\n"
            f"Full payload: {payload_url}"
        )
        agent = await resolve_agent(row.creator_agent_id)
        zulip_message_id: int | None = None
        if agent is not None:
            agent_client = make_agent_client(agent.zulip_bot_email, agent.zulip_api_key)
            try:
                zulip_message_id = await agent_client.send_message(
                    row.source_channel, row.source_topic, message
                )
            except Exception:  # noqa: BLE001 - a posting failure must not lose the submission
                logger.exception("Failed to post artifact summary for %s", artifact_id)

        if agent is not None and zulip_message_id is not None:
            await enqueue_turn(
                agent_id=row.creator_agent_id,
                channel=row.source_channel,
                topic=row.source_topic,
                content=(
                    f'Interactive response submitted for "{row.title}".\n'
                    f"Summary: {summary_text}\n"
                    f"Full payload artifact: {artifact_id}\n"
                    f"Submission id: {body.submission_id}"
                ),
                source=InteractiveSubmissionSource(
                    artifact_id=artifact_id,
                    submission_id=body.submission_id,
                    zulip_message_id=zulip_message_id,
                ),
            )

        return JSONResponse({
            "outcome": "accepted",
            "summary": summary_text,
            "summary_status": summary_status,
        })

    return router
