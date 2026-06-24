"""Debug runtime: echo the fully assembled turn context back into chat.

A relay agent (runtime_kind="relay") performs no LLM call and no tool work.
It returns the exact context the shared pipeline handed it — the
build_context_prompt() system prompt plus the new user message — so the
context assembly documented in context.md is directly observable end-to-end.
Flag-gated off by default; see config.relay_runner_enabled.
"""
from __future__ import annotations

from dataclasses import dataclass

from control_plane.runtime.runners.base import RunnerInput

# Zulip messages cap around 10k chars; stay safely under it.
RELAY_MAX_CHARS = 9500


def _truncate_to(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    # reserve room for the "\n…[truncated N chars]" suffix
    cut = max(0, limit - 40)
    return f"{text[:cut]}\n…[truncated {len(text) - cut} chars]"


@dataclass
class RelayRunner:
    """AgentRunner that echoes RunnerInput context instead of invoking a model."""

    async def run(self, inp: RunnerInput) -> str:
        agent_name = getattr(inp.agent, "name", "?")
        location = (
            "direct message"
            if inp.tool_context.channel == "direct"
            else f"#{inp.tool_context.channel} > {inp.tool_context.topic}"
        )
        header = (
            "🛰️ **relay** — context received this turn\n\n"
            f"**runtime_kind:** relay · **agent:** {agent_name} · **at:** {location}"
        )
        user_block = f"## user_message\n```\n{inp.user_message}\n```"

        # Give system_prompt a budget = cap minus the fixed framing (header +
        # user block), so in a normal turn system_prompt absorbs truncation and
        # user_message is shown in full. This bounds system_prompt; the final
        # clamp below is what actually guarantees the overall cap.
        fixed = f"{header}\n\n## system_prompt\n```\n\n```\n\n{user_block}"
        budget = RELAY_MAX_CHARS - len(fixed)
        sys_text = _truncate_to(inp.system_prompt, max(0, budget))

        reply = (
            f"{header}\n\n"
            f"## system_prompt\n```\n{sys_text}\n```\n\n"
            f"{user_block}"
        )
        # Backstop: system_prompt is truncated to a budget above, but a pathologically
        # large user_message could still overflow — hard-clamp the whole reply so it
        # always posts to Zulip. In normal turns user_message is small and shown in full.
        return _truncate_to(reply, RELAY_MAX_CHARS)
