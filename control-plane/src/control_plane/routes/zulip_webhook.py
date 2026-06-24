import hmac
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response, status

from control_plane.dedupe import SeenMessages
from control_plane.models import OutgoingWebhookPayload
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.job_queue import Job
from control_plane.services.job_source import ZulipMentionSource, enqueue_agent_turn
from control_plane.services.pool_resolver import PoolBotNoSession

logger = logging.getLogger("control_plane")


def _token_matches(provided: str, expected: str | None) -> bool:
    """Authenticate a Zulip outgoing-webhook token.

    Fails closed when the expected secret is empty/None: an agent or pool bot
    whose ``zulip_outgoing_token`` is blank (e.g. an auto-provisioned bot whose
    token has not been set yet, see ``routes/agents.py``) must NEVER be
    authenticated by a forged webhook — a naive ``"" == ""`` would let an
    unauthenticated caller impersonate that bot's sender. Uses
    ``hmac.compare_digest`` so a real token cannot be recovered via a timing
    side-channel (same pattern as ``services/artifact_store.py``).
    """
    if not expected:
        return False
    return hmac.compare_digest(provided, expected)


def _ok() -> Response:
    return Response(status_code=status.HTTP_200_OK, content="{}", media_type="application/json")


def _is_direct_message(display_recipient: object, message_type: str | None) -> bool:
    return message_type in {"direct", "private"} or isinstance(display_recipient, list)


def _direct_recipient_ids(agent: ResolvedAgent, payload: OutgoingWebhookPayload) -> list[int]:
    display_recipient = payload.message.display_recipient
    if not isinstance(display_recipient, list):
        return [payload.message.sender_id] if payload.message.sender_id is not None else []

    recipient_ids = [
        recipient.id
        for recipient in display_recipient
        if recipient.email != agent.zulip_bot_email and recipient.id != agent.zulip_bot_id
    ]
    if not recipient_ids and payload.message.sender_id is not None:
        recipient_ids.append(payload.message.sender_id)
    return recipient_ids


def build_webhook_router(
    resolve_agent_by_email: Callable[[str], Awaitable[ResolvedAgent | None]],
    make_agent_client: Callable[[str, str], object],
    enqueue_job: Callable[[Job], Awaitable[None]],
    write_event: Callable[..., Awaitable[None]],
    resolve_pool_bot_turn: Callable[[str, str, str], Awaitable[object]] | None = None,
) -> APIRouter:
    router = APIRouter()
    seen = SeenMessages()

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/zulip/incoming")
    async def incoming(request: Request) -> Response:
        payload = OutgoingWebhookPayload.model_validate(await request.json())
        bot_email = payload.bot_email or ""

        # Per-agent tokens mean we must resolve the agent before validating the
        # token, so the order here differs from a single-global-token design.
        agent = await resolve_agent_by_email(bot_email)
        if agent is None:
            # Pool-bot path: @-mentions to worker bots not in AgentRow. Stream only; DMs ignored.
            if (
                resolve_pool_bot_turn is not None
                and not _is_direct_message(payload.message.display_recipient, payload.message.type)
            ):
                _channel = str(payload.message.display_recipient)
                _topic = payload.message.subject
                resolution = await resolve_pool_bot_turn(bot_email, _channel, _topic)
                if resolution is not None:
                    if not _token_matches(payload.token, resolution.outgoing_token):
                        return Response(status_code=status.HTTP_403_FORBIDDEN)
                    if not seen.mark(payload.message.id):
                        return _ok()
                    if isinstance(resolution, PoolBotNoSession):
                        return _ok()
                    pool_client = make_agent_client(
                        resolution.agent.zulip_bot_email, resolution.agent.zulip_api_key
                    )
                    await pool_client.add_reaction(payload.message.id, "+1")
                    await enqueue_agent_turn(
                        agent_id=resolution.agent.id,
                        channel=_channel,
                        topic=_topic,
                        content=payload.message.content,
                        session_id=resolution.session_id,
                        source=ZulipMentionSource(
                            message_id=payload.message.id,
                            sender_email=payload.message.sender_email,
                        ),
                        write_event=write_event,
                        enqueue_job=enqueue_job,
                    )
                    return _ok()
            logger.info("Webhook for unknown bot_email=%s ignored", bot_email)
            await write_event(
                actor_type="system",
                event_type="unknown_bot",
                payload={"bot_email": bot_email},
            )
            return _ok()

        if not _token_matches(payload.token, agent.zulip_outgoing_token):
            logger.warning("Rejected webhook with invalid token for %s", bot_email)
            return Response(status_code=status.HTTP_403_FORBIDDEN)

        if not getattr(agent, "enabled", True):
            logger.info("Webhook for disabled agent %s ignored", bot_email)
            await write_event(
                actor_type="system",
                event_type="disabled_agent_ignored",
                payload={"bot_email": bot_email},
                related_agent_id=agent.id,
                source_message_id=payload.message.id,
            )
            return _ok()

        if agent.is_bastion and payload.message.sender_email:
            sender_agent = await resolve_agent_by_email(payload.message.sender_email)
            if sender_agent is not None:
                logger.warning(
                    "Blocked agent->bastion invocation from %s",
                    payload.message.sender_email,
                )
                await write_event(
                    actor_type="system",
                    event_type="bastion_invocation_blocked",
                    payload={"sender_email": payload.message.sender_email},
                    related_agent_id=agent.id,
                    source_message_id=payload.message.id,
                )
                return _ok()

        if not seen.mark(payload.message.id):
            logger.info("Duplicate delivery ignored: message_id=%s", payload.message.id)
            return _ok()

        client = make_agent_client(agent.zulip_bot_email, agent.zulip_api_key)
        await client.add_reaction(payload.message.id, "+1")
        if _is_direct_message(payload.message.display_recipient, payload.message.type):
            direct_recipient_ids = _direct_recipient_ids(agent, payload)
            if not direct_recipient_ids:
                logger.warning(
                    "Direct webhook for %s has no non-bot recipients; ignored",
                    bot_email,
                )
                await write_event(
                    actor_type="system",
                    event_type="direct_message_recipient_error",
                    payload={"bot_email": bot_email, "message_id": payload.message.id},
                    related_agent_id=agent.id,
                    source_message_id=payload.message.id,
                )
                return _ok()
            await enqueue_agent_turn(
                agent_id=agent.id,
                channel="direct",
                topic="",
                content=payload.message.content,
                conversation_type="direct",
                direct_recipient_ids=direct_recipient_ids,
                source=ZulipMentionSource(
                    message_id=payload.message.id, sender_email=payload.message.sender_email
                ),
                write_event=write_event,
                enqueue_job=enqueue_job,
            )
            return _ok()

        await enqueue_agent_turn(
            agent_id=agent.id,
            channel=str(payload.message.display_recipient),
            topic=payload.message.subject,
            content=payload.message.content,
            source=ZulipMentionSource(
                message_id=payload.message.id, sender_email=payload.message.sender_email
            ),
            write_event=write_event,
            enqueue_job=enqueue_job,
        )
        return _ok()

    return router
