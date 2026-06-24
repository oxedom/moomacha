import uuid
from dataclasses import dataclass

from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.pool_store import PoolBotCreds, PoolStore
from control_plane.services.session_store import SessionStore


@dataclass
class PoolBotNoSession:
    """This email is a pool bot, but it has no live/dormant session for the topic.
    The webhook should return 200 silently (user can DM the bastion to revive)."""
    outgoing_token: str


@dataclass
class PoolBotTurnResult:
    """Pool bot with an active (live or just-reopened) session. Ready to run a turn."""
    outgoing_token: str
    agent: ResolvedAgent
    session_id: uuid.UUID


PoolBotResolution = PoolBotTurnResult | PoolBotNoSession | None


def agent_from_snapshot(
    snapshot: dict,
    creds: PoolBotCreds,
    pool_bot_uuid: uuid.UUID,
    zulip_bot_id: int,
    channel: str,
) -> ResolvedAgent:
    """Build a ResolvedAgent from a session's frozen archetype_snapshot + pool bot creds."""
    return ResolvedAgent(
        id=pool_bot_uuid,
        name=snapshot.get("name", "worker"),
        persona=snapshot.get("persona", ""),
        model_id=snapshot.get("model_id", "gpt-4o"),
        zulip_bot_id=zulip_bot_id,
        zulip_bot_email=creds.bot_email,
        zulip_api_key=creds.api_key,
        zulip_outgoing_token=creds.outgoing_token,
        context_message_count=snapshot.get("context_message_count", 20),
        readable_channels=[channel],
        allowed_tools=snapshot.get("allowed_tools", []),
        knowledge_artifact_ids=snapshot.get("knowledge_artifact_ids", []),
        is_bastion=False,
        # Pool bots default to the DeepAgents runtime — an intentional divergence
        # from ResolvedAgent's model-level default of "openai_tool_loop".
        runtime_kind=snapshot.get("runtime_kind", "deepagents"),
        runtime_config=snapshot.get("runtime_config", {}),
    )


async def resolve_pool_bot_for_webhook(
    pool_store: PoolStore,
    session_store: SessionStore,
    bot_email: str,
    channel: str,
    topic: str,
) -> PoolBotResolution:
    """Resolve an incoming Zulip webhook message to a pool-bot session turn.

    Returns:
        None              — bot_email is not a pool bot; webhook falls through to unknown_bot.
        PoolBotNoSession  — it IS a pool bot but has no active session for this topic.
        PoolBotTurnResult — pool bot with a live session (dormant sessions are reopened here).
    """
    bot = await pool_store.find_by_email(bot_email)
    if bot is None:
        return None

    creds = await pool_store.resolve_creds(bot.id)
    if creds is None:
        return None  # broken row; treat as unknown

    session = await session_store.resolve_for_topic(channel, topic)
    if session is None:
        return PoolBotNoSession(outgoing_token=creds.outgoing_token)

    if session.state == "dormant":
        session = await session_store.reopen(session.id)
        if session is None:
            # Row vanished between fetch and reopen; treat as no active session.
            return PoolBotNoSession(outgoing_token=creds.outgoing_token)

    agent = agent_from_snapshot(session.archetype_snapshot, creds, bot.id, bot.zulip_bot_id, channel)
    return PoolBotTurnResult(
        outgoing_token=creds.outgoing_token,
        agent=agent,
        session_id=session.id,
    )
