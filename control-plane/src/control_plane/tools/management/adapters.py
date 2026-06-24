"""Bridge management handlers (dict-args, ManagementToolContext, -> str) into the
runtime tool convention (Pydantic input model, ToolContext, -> ToolResult).

The handlers in tools.py stay untouched; these adapters give each one a Pydantic
input model (for OpenAI schema + arg validation) and wrap its string result.
Registered with management=True so the registry/runtime auto-allow them iff
ctx.agent.is_bastion (privilege follows the flag).
"""

from pydantic import BaseModel, Field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult
from control_plane.tools.management import tools


class _ListAgentsArgs(BaseModel):
    pass


class _NameArg(BaseModel):
    name: str = Field(description="The agent's name (exact or unambiguous partial match).")


class _CreateAgentArgs(BaseModel):
    name: str
    persona: str
    model_id: str | None = None
    readable_channels: list[str] = Field(default_factory=list)
    zulip_bot_id: int | None = None
    zulip_bot_email: str | None = None
    zulip_api_key: str | None = None
    zulip_outgoing_token: str | None = None


class _UpdateAgentArgs(BaseModel):
    name: str
    persona: str | None = None
    model_id: str | None = None
    readable_channels: list[str] | None = None
    context_message_count: int | None = None


class _AttachBotArgs(BaseModel):
    name: str
    outgoing_token: str
    api_key: str | None = None
    bot_id: int | None = None
    bot_email: str | None = None


class _SetBotAvatarArgs(BaseModel):
    name: str = Field(description="The agent whose Zulip bot avatar should be set.")
    image_url: str = Field(description="Publicly fetchable URL of the image to upload as the avatar.")


def _adapt(handler):
    async def adapter(parsed: BaseModel, ctx: ToolContext) -> ToolResult:
        if ctx.management is None:
            return ToolResult(ok=False, content="Management tools require the bastion context.")
        text = await handler(parsed.model_dump(exclude_none=True), ctx.management)
        return ToolResult(ok=True, content=text)

    return adapter


def register_management_tools(registry: ToolRegistry) -> None:
    registry.register(
        "list_agents", "List all agents.", _ListAgentsArgs, _adapt(tools.list_agents), management=True
    )
    registry.register(
        "get_agent", "Show one agent's configuration.", _NameArg, _adapt(tools.get_agent), management=True
    )
    registry.register(
        "create_agent",
        "Create a new agent (auto-provisions a Zulip bot unless creds are supplied).",
        _CreateAgentArgs,
        _adapt(tools.create_agent),
        management=True,
    )
    registry.register(
        "update_agent",
        "Edit an existing agent's persona/model/channels/context size.",
        _UpdateAgentArgs,
        _adapt(tools.update_agent),
        management=True,
    )
    registry.register(
        "enable_agent",
        "Enable an agent so it responds to mentions.",
        _NameArg,
        _adapt(tools.enable_agent),
        management=True,
    )
    registry.register(
        "disable_agent",
        "Disable an agent (requires a 'confirm <name>' message).",
        _NameArg,
        _adapt(tools.disable_agent),
        management=True,
    )
    registry.register(
        "provision_bot",
        "Provision a Zulip bot for an existing agent.",
        _NameArg,
        _adapt(tools.provision_bot),
        management=True,
    )
    registry.register(
        "attach_bot",
        "Attach Zulip bot credentials (outgoing token, etc.) to an existing agent.",
        _AttachBotArgs,
        _adapt(tools.attach_bot),
        management=True,
    )
    registry.register(
        "set_bot_avatar",
        "Set an agent's Zulip bot avatar from an image URL.",
        _SetBotAvatarArgs,
        _adapt(tools.set_bot_avatar),
        management=True,
    )
    registry.register(
        "delete_agent",
        "Delete an agent permanently (requires a 'confirm <name>' message).",
        _NameArg,
        _adapt(tools.delete_agent),
        management=True,
    )
