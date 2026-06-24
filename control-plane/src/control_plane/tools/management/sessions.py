import logging

from control_plane.schemas.archetype import ArchetypeDefinition
from control_plane.services.session_lifecycle import reclaim_for_capacity
from control_plane.tools.management.context import ManagementToolContext
from control_plane.zulip_client import ZulipClient

logger = logging.getLogger("control_plane")


def _stores_ready(ctx: ManagementToolContext) -> str | None:
    if ctx.archetypes is None or ctx.pool is None or ctx.sessions is None:
        return "Session tools are not available (cattle stores not wired)."
    return None


async def search_archetypes(args: dict, ctx: ManagementToolContext) -> str:
    err = _stores_ready(ctx)
    if err:
        return err
    matches = await ctx.archetypes.search(args.get("query", ""))
    if not matches:
        return "No archetypes match that query."
    return "Archetypes:\n" + "\n".join(
        f"- {d.name} (model={d.model_id}, tools={len(d.allowed_tools)})" for d in matches
    )


async def build_archetype(args: dict, ctx: ManagementToolContext) -> str:
    err = _stores_ready(ctx)
    if err:
        return err
    name, persona = args.get("name"), args.get("persona")
    if not name or not persona:
        return "build_archetype requires both 'name' and 'persona'."
    if await ctx.archetypes.get_by_name(name) is not None:
        return f"An archetype named '{name}' already exists."
    defn = ArchetypeDefinition(
        name=name,
        persona=persona,
        model_id=args.get("model_id") or ctx.default_model,
        allowed_tools=args.get("allowed_tools") or [],
    )
    saved = await ctx.archetypes.create(defn)
    return f"Saved archetype '{saved.name}' (model={saved.model_id})."


async def _snapshot_for(args: dict, ctx: ManagementToolContext) -> tuple[dict | None, str | None]:
    """Resolve a saved archetype by name, or build a one-off snapshot from 'persona'."""
    name = args.get("archetype")
    if name:
        defn = await ctx.archetypes.get_by_name(name)
        if defn is None:
            return None, f"No archetype named '{name}'. Build one first, or pass a 'persona' for a one-off."
        return defn.model_dump(), None
    persona = args.get("persona")
    if not persona:
        return None, "spin_up_session needs either an 'archetype' name or a 'persona' for a one-off."
    one_off = ArchetypeDefinition(
        name=args.get("display_name") or "one-off",
        persona=persona,
        model_id=args.get("model_id") or ctx.default_model,
        allowed_tools=args.get("allowed_tools") or [],
    )
    return one_off.model_dump(), None


async def spin_up_session(args: dict, ctx: ManagementToolContext) -> str:
    err = _stores_ready(ctx)
    if err:
        return err
    channel, topic = args.get("channel"), args.get("topic")
    display_name = args.get("display_name")
    if not channel or not topic or not display_name:
        return "spin_up_session requires 'channel', 'topic', and 'display_name'."
    if await ctx.sessions.resolve_for_topic(channel, topic) is not None:
        return f"A session already exists in {channel} > {topic}."

    snapshot, snap_err = await _snapshot_for(args, ctx)
    if snap_err:
        return snap_err

    session = await ctx.sessions.create(
        channel=channel, topic=topic, snapshot=snapshot, pool_bot_id=None,
        archetype_name=args.get("archetype"), state="provisioning",
    )
    leased = await ctx.pool.lease(session_id=session.id, display_name=display_name)
    if leased is None:
        if await reclaim_for_capacity(ctx.pool, ctx.sessions) is not None:
            leased = await ctx.pool.lease(session_id=session.id, display_name=display_name)
    if leased is None:
        await ctx.sessions.close(session.id)
        return "No pool bots are free and none could be reclaimed. Provision another worker bot."

    # Bind + Zulip birth are wrapped so any in-process failure rolls back to a clean,
    # re-runnable topic. mark_live is the commit point: a 'live' session is always
    # fully born and bound. (A process crash mid-birth leaves a 'provisioning' row,
    # cleaned by recover_pool_consistency on next boot.)
    try:
        await ctx.sessions.bind_pool_bot(session.id, leased.id)
        if ctx.admin_client is not None:
            creds = await ctx.pool.resolve_creds(leased.id)
            if creds is None:
                logger.warning(
                    "spin_up_session: could not resolve creds for pool bot %s; "
                    "skipping Zulip birth (no rename/subscribe/kickoff).",
                    leased.id,
                )
            else:
                await ctx.admin_client.rename_bot(leased.zulip_bot_id, display_name)
                _factory = ctx.make_zulip_client or (lambda s, e, k: ZulipClient(s, e, k))
                bot_client = _factory(ctx.admin_client.site, creds.bot_email, creds.api_key)
                await bot_client.subscribe_to_channel(channel)
                await bot_client.send_message(
                    channel, topic, f"Hi! I'm {display_name}. Let's get started."
                )
        await ctx.sessions.mark_live(session.id)
    except Exception:
        logger.exception(
            "spin_up_session: birth failed for %s > %s; rolling back", channel, topic
        )
        await ctx.pool.release(leased.id)
        await ctx.sessions.close(session.id)
        return (
            "spin_up failed during setup and was rolled back; "
            "the topic is clean and can be retried."
        )
    return f"Spun up '{display_name}' in {channel} > {topic} on bot {leased.zulip_bot_email}."


async def close_session(args: dict, ctx: ManagementToolContext) -> str:
    err = _stores_ready(ctx)
    if err:
        return err
    channel, topic = args.get("channel"), args.get("topic")
    if not channel or not topic:
        return "close_session requires 'channel' and 'topic'."
    session = await ctx.sessions.resolve_for_topic(channel, topic)
    if session is None:
        return f"No open session in {channel} > {topic}."
    if session.pool_bot_id is not None:
        await ctx.pool.release(session.pool_bot_id)
    await ctx.sessions.close(session.id)
    return f"Closed the session in {channel} > {topic} and returned its bot to the pool."
