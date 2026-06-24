"""The create_interactive_response platform tool.

Mirrors the scheduling tools: the ArtifactStore and config (base_url, expiry
clamps, size cap) are injected by closure. title/html come from the model;
channel/topic/agent/source_message_id come from ctx and are never model-controlled.
On success the tool creates the artifact, auto-posts the signed link to the current
topic via ctx.zulip, emits interactive_artifact_posted, and returns the URL."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult
from control_plane.services.artifact_store import ArtifactStore


def _now_utc() -> datetime:
    return datetime.now(UTC)


class CreateInteractiveResponseInput(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="Short title shown in chat and on the page.")
    html: str = Field(
        min_length=1,
        description=(
            "Complete single-page HTML document. SUBMIT by calling the injected async helper "
            "`await window.AgentUI.submit(payloadObject)` — do NOT call window.__AGENT_UI__.submitUrl(...); "
            "submitUrl is a STRING (the endpoint), not a function. The helper returns "
            "{outcome, summary}; show that to the user. Minimal correct pattern:\n"
            "<form id=\"f\"><input name=\"who\"><button>Submit</button></form>\n"
            "<script>f.addEventListener('submit', async e => { e.preventDefault();\n"
            "  const res = await window.AgentUI.submit({who: f.who.value});\n"
            "  document.body.innerHTML = '<p>Submitted: ' + res.summary + '</p>'; });</script>\n"
            "For styling, load Tailwind ONLY from https://cdn.tailwindcss.com "
            "(<script src=\"https://cdn.tailwindcss.com\"></script>); other CDNs are blocked by CSP."
        ),
    )
    expires_in_minutes: int | None = Field(
        default=None, ge=1,
        description="How long the link stays open. Defaults to 2 days; clamped to a 14-day max.",
    )


def _humanize_expiry(minutes: int) -> str:
    if minutes % 1440 == 0:
        days = minutes // 1440
        return f"{days} day{'s' if days != 1 else ''}"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minutes"


async def _create_interactive_response(
    inp: CreateInteractiveResponseInput,
    ctx: ToolContext,
    store: ArtifactStore,
    base_url: str,
    default_expiry_minutes: int,
    max_expiry_minutes: int,
    max_html_bytes: int,
    clock: Callable[[], datetime],
) -> ToolResult:
    # v1 artifacts bind to a stream topic and auto-post the link there. Direct
    # messages have no stream to post to, so refuse cleanly instead of failing the
    # Zulip send. (Tracked for a future DM-aware version.)
    if ctx.conversation_type != "stream":
        return ToolResult(
            ok=False,
            content="Interactive responses aren't supported in direct messages yet — "
            "ask me in a channel topic instead.",
        )
    if len(inp.html.encode("utf-8")) > max_html_bytes:
        return ToolResult(ok=False, content=f"HTML too large (max {max_html_bytes} bytes).")

    minutes = inp.expires_in_minutes or default_expiry_minutes
    if minutes > max_expiry_minutes:
        minutes = max_expiry_minutes  # clamp (decision: clamp, not reject)
    expires_at = clock() + timedelta(minutes=minutes)

    created = await store.create(
        title=inp.title,
        html_body=inp.html,
        creator_agent_id=ctx.agent.id,
        source_channel=ctx.channel,
        source_topic=ctx.topic,
        source_message_id=ctx.source_message_id,
        expires_at=expires_at,
    )
    url = f"{base_url.rstrip('/')}/ui/artifacts/{created.row.id}?token={created.raw_token}"
    agent_name = getattr(ctx.agent, "name", "an agent")
    chat_message = (
        f"Interactive response from {agent_name}: {inp.title}\n"
        f"{url}\n\n"
        f"Expires in {_humanize_expiry(minutes)}."
    )
    await ctx.zulip.send_message(ctx.channel, ctx.topic, chat_message)
    await store._emit("interactive_artifact_posted", created.row)
    return ToolResult(
        ok=True,
        content=f"Posted interactive response '{inp.title}' to #{ctx.channel} > {ctx.topic}. Link: {url}",
    )


def register_interactive_response_tools(
    registry: ToolRegistry,
    store: ArtifactStore,
    *,
    base_url: str,
    default_expiry_minutes: int,
    max_expiry_minutes: int,
    max_html_bytes: int,
    clock: Callable[[], datetime] = _now_utc,
) -> None:
    registry.register(
        "create_interactive_response",
        "Create a shareable interactive HTML response (a form/UI) and auto-post its link to THIS topic. Humans open it, fill it out, and submit once; the submission posts a summary back here and resumes you.",
        CreateInteractiveResponseInput,
        lambda inp, ctx: _create_interactive_response(
            inp, ctx, store, base_url, default_expiry_minutes,
            max_expiry_minutes, max_html_bytes, clock,
        ),
    )
