import httpx

from control_plane.schemas.agents import AgentCreate, AgentUpdate
from control_plane.services.exceptions import AgentAlreadyExistsError
from control_plane.tools.management.confirm import confirmation_satisfied
from control_plane.tools.management.context import ManagementToolContext, resolve_one
from control_plane.zulip_avatar import set_bot_avatar as _upload_bot_avatar


async def _emit(ctx: ManagementToolContext, event_type: str, agent_id) -> None:
    """Write an audit event for a management mutation, if a session factory is wired."""
    if ctx.session_factory is None:
        return
    from control_plane.events.writer import write_event

    await write_event(
        ctx.session_factory,
        actor_type="agent",
        event_type=event_type,
        payload={},
        related_agent_id=agent_id,
    )


async def list_agents(args: dict, ctx: ManagementToolContext) -> str:
    agents = await ctx.registry.list()
    if not agents:
        return "There are no agents yet."
    lines = [
        f"- {a.name} (model={a.model_id}, status={a.provisioning_status}, "
        f"channels={len(a.readable_channels)})"
        for a in agents
    ]
    return "Agents:\n" + "\n".join(lines)


async def get_agent(args: dict, ctx: ManagementToolContext) -> str:
    name = args.get("name", "")
    agent, err = await resolve_one(ctx, name)
    if err:
        return err
    channels = ", ".join(agent.readable_channels) or "(none)"
    return (
        f"{agent.name}\n"
        f"  model: {agent.model_id}\n"
        f"  bot email: {agent.zulip_bot_email}\n"
        f"  status: {agent.provisioning_status}\n"
        f"  context messages: {agent.context_message_count}\n"
        f"  readable channels: {channels}"
    )


async def create_agent(args: dict, ctx: ManagementToolContext) -> str:
    name = args.get("name")
    persona = args.get("persona")
    if not name or not persona:
        return "create_agent requires both 'name' and 'persona'."
    model_id = args.get("model_id") or ctx.default_model
    readable_channels = args.get("readable_channels") or []

    manual = all(
        args.get(k)
        for k in ("zulip_bot_id", "zulip_bot_email", "zulip_api_key", "zulip_outgoing_token")
    )
    if manual:
        create = AgentCreate(
            name=name,
            persona=persona,
            model_id=model_id,
            readable_channels=readable_channels,
            zulip_bot_id=args["zulip_bot_id"],
            zulip_bot_email=args["zulip_bot_email"],
            zulip_api_key=args["zulip_api_key"],
            zulip_outgoing_token=args["zulip_outgoing_token"],
        )
        followup = ""
    else:
        short_name = name.lower().replace(" ", "-") + "-bot"
        try:
            result = await ctx.admin_client.provision_bot(
                full_name=name,
                short_name=short_name,
                payload_url=ctx.payload_url,
                channels=readable_channels,
            )
        except Exception as exc:  # noqa: BLE001 - surface provisioning failure to chat
            return f"Provisioning failed for '{name}': {exc}"
        create = AgentCreate(
            name=name,
            persona=persona,
            model_id=model_id,
            readable_channels=readable_channels,
            zulip_bot_id=result.bot_id,
            zulip_bot_email=result.bot_email,
            zulip_api_key=result.api_key,
            zulip_outgoing_token=result.outgoing_token or "",
        )
        if result.outgoing_token:
            followup = " Its Zulip bot is provisioned and ready to receive mentions."
        else:
            followup = (
                f" The bot was created but its outgoing-webhook token could not be captured, "
                f"so '{name}' will not receive webhooks until you run: attach_bot {name} <token>."
            )

    try:
        created = await ctx.registry.create(create)
    except AgentAlreadyExistsError as exc:
        return f"Could not create '{name}': {exc}"
    await _emit(ctx, "agent_created", created.id)
    return f"Created agent {created.name} (model={created.model_id}).{followup}"


async def delete_agent(args: dict, ctx: ManagementToolContext) -> str:
    name = args.get("name", "")
    agent, err = await resolve_one(ctx, name)
    if err:
        return err
    if not confirmation_satisfied(ctx.invoking_message_text, agent.name):
        return (
            f"⚠️ This permanently deletes agent **{agent.name}** "
            f"(bot {agent.zulip_bot_email}). Reply with `confirm {agent.name}` to proceed."
        )
    await ctx.registry.delete(agent.id)
    await _emit(ctx, "agent_deleted", agent.id)
    return f"Deleted agent {agent.name}."


async def enable_agent(args: dict, ctx: ManagementToolContext) -> str:
    agent, err = await resolve_one(ctx, args.get("name", ""))
    if err:
        return err
    await ctx.registry.set_enabled(agent.id, True)
    await _emit(ctx, "agent_enabled", agent.id)
    return f"Enabled agent {agent.name}."


async def disable_agent(args: dict, ctx: ManagementToolContext) -> str:
    agent, err = await resolve_one(ctx, args.get("name", ""))
    if err:
        return err
    if not confirmation_satisfied(ctx.invoking_message_text, agent.name):
        return (
            f"⚠️ This disables agent **{agent.name}** (it will stop responding to mentions). "
            f"Reply with `confirm {agent.name}` to proceed."
        )
    await ctx.registry.set_enabled(agent.id, False)
    await _emit(ctx, "agent_disabled", agent.id)
    return f"Disabled agent {agent.name}."


async def update_agent(args: dict, ctx: ManagementToolContext) -> str:
    agent, err = await resolve_one(ctx, args.get("name", ""))
    if err:
        return err
    updated = await ctx.registry.update(
        agent.id,
        AgentUpdate(
            persona=args.get("persona"),
            model_id=args.get("model_id"),
            readable_channels=args.get("readable_channels"),
            context_message_count=args.get("context_message_count"),
        ),
    )
    if updated is None:
        return f"Could not update '{agent.name}'."
    await _emit(ctx, "agent_updated", agent.id)
    return f"Updated agent {updated.name}."


async def attach_bot(args: dict, ctx: ManagementToolContext) -> str:
    agent, err = await resolve_one(ctx, args.get("name", ""))
    if err:
        return err
    token = args.get("outgoing_token")
    if not token:
        return "attach_bot requires 'outgoing_token'."
    await ctx.registry.update(
        agent.id,
        AgentUpdate(
            zulip_outgoing_token=token,
            zulip_api_key=args.get("api_key"),
            zulip_bot_id=args.get("bot_id"),
            zulip_bot_email=args.get("bot_email"),
            provisioning_status="active",
        ),
    )
    await _emit(ctx, "bot_attached", agent.id)
    return f"Attached bot credentials to {agent.name}; it can now receive webhooks."


async def set_bot_avatar(args: dict, ctx: ManagementToolContext) -> str:
    agent, err = await resolve_one(ctx, args.get("name", ""))
    if err:
        return err
    image_url = args.get("image_url")
    if not image_url:
        return "set_bot_avatar requires an 'image_url' pointing at the image to upload."
    if not agent.zulip_bot_email:
        return f"Agent '{agent.name}' has no Zulip bot, so it has no avatar to set."
    # Avatars can only be set as the bot itself, so fetch its decrypted creds.
    resolved = await ctx.registry.resolve_by_bot_email(agent.zulip_bot_email)
    if resolved is None:
        return f"Could not resolve Zulip credentials for '{agent.name}'."
    try:
        async with httpx.AsyncClient() as http:
            img = await http.get(image_url)
            img.raise_for_status()
        avatar_url = await _upload_bot_avatar(
            site=ctx.admin_client.site,
            email=resolved.zulip_bot_email,
            api_key=resolved.zulip_api_key,
            image_bytes=img.content,
            content_type=img.headers.get("content-type", "image/png"),
        )
    except Exception as exc:  # noqa: BLE001 - surface upload failure to chat
        return f"Could not set avatar for '{agent.name}': {exc}"
    await _emit(ctx, "bot_avatar_updated", agent.id)
    return f"Updated {agent.name}'s avatar: {avatar_url}"


async def provision_bot(args: dict, ctx: ManagementToolContext) -> str:
    agent, err = await resolve_one(ctx, args.get("name", ""))
    if err:
        return err
    short_name = agent.name.lower().replace(" ", "-") + "-bot"
    try:
        result = await ctx.admin_client.provision_bot(
            full_name=agent.name,
            short_name=short_name,
            payload_url=ctx.payload_url,
            channels=agent.readable_channels,
        )
    except Exception as exc:  # noqa: BLE001 - surface provisioning failure to chat
        return f"Provisioning failed for '{agent.name}': {exc}"
    await ctx.registry.update(
        agent.id,
        AgentUpdate(
            zulip_bot_id=result.bot_id,
            zulip_bot_email=result.bot_email,
            zulip_api_key=result.api_key,
            zulip_outgoing_token=result.outgoing_token or None,
            provisioning_status="active" if result.outgoing_token else "awaiting_token",
        ),
    )
    await _emit(ctx, "bot_provisioned", agent.id)
    if result.outgoing_token:
        return f"Provisioned bot for {agent.name}; its webhook token was captured and it is ready."
    return (
        f"Provisioned bot for {agent.name}, but its outgoing-webhook token could not be captured; "
        f"run: attach_bot {agent.name} <token>."
    )
