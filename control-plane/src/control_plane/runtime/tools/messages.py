from pydantic import BaseModel, Field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult
from control_plane.services.context_assembly import truncate

READ_CAP = 100  # hard ceiling on messages fetched per call


class ReadTopicInput(BaseModel):
    channel: str = Field(description="Channel (stream) name to read from.")
    topic: str = Field(description="Topic within the channel to read.")
    limit: int = Field(default=20, description="How many recent messages to fetch.")


class ReadChannelInput(BaseModel):
    channel: str = Field(description="Channel (stream) name to read from.")
    limit: int = Field(default=20, description="How many recent messages to fetch.")


def _channel_allowed(readable_channels: list[str], channel: str) -> bool:
    return "*" in readable_channels or channel in readable_channels


def _format(messages: list[dict]) -> str:
    if not messages:
        return "(no recent messages)"
    return "\n".join(
        f"{m.get('sender_full_name', 'unknown')}: {truncate(m.get('content', ''))}"
        for m in messages
    )


async def read_topic(inp: ReadTopicInput, ctx: ToolContext) -> ToolResult:
    if not _channel_allowed(ctx.agent.readable_channels, inp.channel):
        return ToolResult(ok=False, content=f"Not permitted to read #{inp.channel}.")
    messages = await ctx.zulip.get_messages(
        inp.channel, inp.topic, num_before=min(inp.limit, READ_CAP)
    )
    return ToolResult(ok=True, content=_format(messages))


async def read_channel(inp: ReadChannelInput, ctx: ToolContext) -> ToolResult:
    if not _channel_allowed(ctx.agent.readable_channels, inp.channel):
        return ToolResult(ok=False, content=f"Not permitted to read #{inp.channel}.")
    messages = await ctx.zulip.get_channel_messages(
        inp.channel, num_before=min(inp.limit, READ_CAP)
    )
    return ToolResult(ok=True, content=_format(messages))


def register_message_tools(registry: ToolRegistry) -> None:
    registry.register(
        "read_topic",
        "Read recent messages in a specific channel and topic.",
        ReadTopicInput,
        read_topic,
    )
    registry.register(
        "read_channel",
        "Read recent messages across a channel (all topics).",
        ReadChannelInput,
        read_channel,
    )
