"""Pure renderer: a Claw object -> the prose stored in agents.persona.

No I/O, no network. The only place that knows the prose layout, so claw.json
stays presentation-free and the model gets natural prose, not a JSON blob.
"""

ZULIP_FORMATTING = (
    "You operate in Zulip topics. Use Zulip-flavored Markdown: **bold**, *italic*, "
    "`- ` bullets, fenced ``` code blocks, and #**channel** / @**Name** mentions. "
    "Keep replies tight."
)


def render_persona(obj: dict) -> str:
    parts: list[str] = [f"# {obj['name']}\n\n{obj['identity'].strip()}"]

    voice = obj.get("voice", [])
    if voice:
        bullets = "\n".join(f"- {v}" for v in voice)
        parts.append("## How I communicate\n\n" + bullets + "\n\n" + ZULIP_FORMATTING)

    prefs = obj.get("preferences", [])
    if prefs:
        lines = []
        for p in prefs:
            detail = p["detail"]
            if p.get("deferred"):
                detail = f"{detail} (Intended rhythm — this capability is not yet wired in this system.)"
            lines.append(f"- **{p['topic']}:** {detail}")
        parts.append("## Standing preferences\n\n" + "\n".join(lines))

    facts = obj.get("memory", {}).get("distilled_facts", [])
    if facts:
        bullets = "\n".join(f"- {f}" for f in facts)
        parts.append("## What I remember\n\n" + bullets)

    artifacts = obj.get("artifacts", [])
    if artifacts:
        lines = [f"- **{a['name']}** ({a['kind']}) — `{a['path']}`" for a in artifacts]
        parts.append("## Things I've made\n\n" + "\n".join(lines))

    return "\n\n".join(parts) + "\n"
