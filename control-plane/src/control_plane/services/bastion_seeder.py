import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import AgentRow
from control_plane.events.writer import write_event
from control_plane.services.crypto import SecretBox
from control_plane.tools.management.persona import DEFAULT_BASTION_PERSONA

logger = logging.getLogger("control_plane")


async def seed_bastion(
    session_factory: async_sessionmaker[AsyncSession],
    settings,
    secret_box: SecretBox,
    admin_client=None,
) -> uuid.UUID | None:
    """Idempotently seed the bastion agent. Returns its id, or None if it could
    not be seeded.

    Two paths, by config:
    - Manual creds (all four BASTION_* set): seeded/refreshed directly, active.
    - Creds absent: skipped with a warning recommending the operator set BASTION_*.
      We deliberately do NOT auto-provision a bot here — Zulip never returns the
      outgoing-webhook token on creation, so auto-provision left a half-broken
      ``awaiting_token`` row and logged a startup traceback. See the bastion-setup
      skill for obtaining the four creds. ``admin_client`` is accepted for backward
      compatibility but no longer used.
    """
    has_creds = bool(
        settings.bastion_bot_id
        and settings.bastion_bot_email
        and settings.bastion_api_key
        and settings.bastion_outgoing_token
    )

    persona = settings.bastion_persona or DEFAULT_BASTION_PERSONA
    model_id = settings.bastion_model_id or "gpt-4o"

    async with session_factory() as session:
        existing = (
            await session.execute(
                select(AgentRow)
                .where(AgentRow.is_bastion == True)  # noqa: E712
                .order_by(AgentRow.created_at)
            )
        ).scalars().first()

        channels = settings.bastion_channel_list

        if existing is None:
            if has_creds:
                row = AgentRow(
                    name=settings.bastion_name,
                    persona=persona,
                    model_id=model_id,
                    zulip_bot_id=settings.bastion_bot_id,
                    zulip_bot_email=settings.bastion_bot_email,
                    zulip_api_key_encrypted=secret_box.encrypt(settings.bastion_api_key),
                    zulip_outgoing_token_encrypted=secret_box.encrypt(
                        settings.bastion_outgoing_token
                    ),
                    readable_channels=channels,
                    provisioning_status="active",
                    is_bastion=True,
                )
            else:
                logger.warning(
                    "Bastion not seeded: BASTION_BOT_ID/BASTION_BOT_EMAIL/BASTION_API_KEY/"
                    "BASTION_OUTGOING_TOKEN are not all set. The bastion management agent is "
                    "recommended — set those env vars (see the bastion-setup skill) to enable it."
                )
                return None
            session.add(row)
            await session.commit()
            await session.refresh(row)
            agent_id, event_type = row.id, "agent_seeded"
        elif has_creds:
            existing.persona = persona
            existing.model_id = model_id
            existing.readable_channels = channels
            existing.zulip_bot_id = settings.bastion_bot_id
            existing.zulip_bot_email = settings.bastion_bot_email
            existing.zulip_api_key_encrypted = secret_box.encrypt(settings.bastion_api_key)
            existing.zulip_outgoing_token_encrypted = secret_box.encrypt(
                settings.bastion_outgoing_token
            )
            await session.commit()
            agent_id, event_type = existing.id, "agent_updated"
        else:
            # Bastion already exists (possibly mid-provision); refresh only the
            # non-credential config so a rename/persona/channel tweak still takes effect.
            existing.persona = persona
            existing.model_id = model_id
            existing.readable_channels = channels
            await session.commit()
            agent_id, event_type = existing.id, "agent_updated"

    await write_event(
        session_factory,
        actor_type="system",
        event_type=event_type,
        payload={"bastion": True},
        related_agent_id=agent_id,
    )
    return agent_id
