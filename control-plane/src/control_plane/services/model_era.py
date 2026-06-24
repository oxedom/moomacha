"""Map a concrete model id to a coarse 'era' tag used to filter skills.

Skills are tagged with an era (e.g. 'opus-4.x'); a skill loads only when its era
matches the agent's model era (or the skill's era is blank = always-on).
"""


def model_era_for(model_id: str) -> str:
    mid = (model_id or "").lower()
    if mid.startswith("claude-opus-4"):
        return "opus-4.x"
    if mid.startswith("claude-sonnet-4"):
        return "sonnet-4.x"
    if mid.startswith("claude-haiku-4"):
        return "haiku-4.x"
    if mid.startswith("gpt-4o"):
        return "gpt-4o"
    return model_id
