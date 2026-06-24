TRUNCATE_LIMIT = 1000
KEEP_CHARS = 200


def truncate(content: str, limit: int = TRUNCATE_LIMIT) -> str:
    if len(content) <= limit:
        return content
    return f"{content[:KEEP_CHARS]}…[truncated, {len(content)} chars]"


def build_context_prompt(
    persona: str,
    messages: list[dict],
    channel: str,
    topic: str,
    tools: list[tuple[str, str]] | None = None,
) -> str:
    lines = [
        f"{m.get('sender_full_name', 'unknown')}: {truncate(m.get('content', ''))}"
        for m in messages
    ]
    history = "\n".join(lines) if lines else "(no recent messages)"
    location = "direct message conversation" if channel == "direct" else f"#{channel} > {topic}"
    tools_section = ""
    if tools:
        tool_lines = "\n".join(f"- {name} — {desc}" for name, desc in tools)
        tools_section = f"## Your tools\n{tool_lines}\n\n"
    return (
        f"{persona}\n\n"
        f"{tools_section}"
        f"## Recent conversation in {location}\n"
        f"{history}\n\n"
        "## Your task\n"
        "Reply to the most recent message addressed to you. "
        "Be concise and helpful."
    )
