"""Bridge the session orchestration handlers into the runtime tool convention,
registered management=True so they are auto-allowed only for the bastion."""

from pydantic import BaseModel, Field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult
from control_plane.tools.management import sessions


class _SearchArchetypesArgs(BaseModel):
    query: str = Field(default="", description="Substring to match against archetype names.")


class _BuildArchetypeArgs(BaseModel):
    name: str
    persona: str
    model_id: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)


class _SpinUpArgs(BaseModel):
    channel: str = Field(description="Target Zulip channel for the new topic.")
    topic: str = Field(description="The topic to open for this session.")
    display_name: str = Field(description="Name the leased bot wears in this topic.")
    archetype: str | None = Field(default=None, description="Saved archetype name to instantiate.")
    persona: str | None = Field(default=None, description="Persona for a one-off (when no archetype).")
    model_id: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)


class _CloseSessionArgs(BaseModel):
    channel: str
    topic: str


def _adapt(handler):
    async def adapter(parsed: BaseModel, ctx: ToolContext) -> ToolResult:
        if ctx.management is None:
            return ToolResult(ok=False, content="Session tools require the bastion context.")
        text = await handler(parsed.model_dump(exclude_none=True), ctx.management)
        return ToolResult(ok=True, content=text)

    return adapter


def register_session_tools(registry: ToolRegistry) -> None:
    registry.register(
        "search_archetypes", "Search saved agent archetypes by name.",
        _SearchArchetypesArgs, _adapt(sessions.search_archetypes), management=True,
    )
    registry.register(
        "build_archetype", "Create and save a reusable agent archetype.",
        _BuildArchetypeArgs, _adapt(sessions.build_archetype), management=True,
    )
    registry.register(
        "spin_up_session",
        "Open a per-topic session: instantiate an archetype (or a one-off persona) and lease a pool bot.",
        _SpinUpArgs, _adapt(sessions.spin_up_session), management=True,
    )
    registry.register(
        "close_session", "Close a topic's session and return its bot to the pool.",
        _CloseSessionArgs, _adapt(sessions.close_session), management=True,
    )
