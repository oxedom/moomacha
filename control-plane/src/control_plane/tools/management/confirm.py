import re

DESTRUCTIVE_TOOLS = {"delete_agent", "disable_agent"}
_CONFIRM_WORD = re.compile(r"\bconfirm\b", re.IGNORECASE)


def requires_confirmation(tool_name: str) -> bool:
    """True if the tool is destructive and needs a confirmed human message."""
    return tool_name in DESTRUCTIVE_TOOLS


def confirmation_satisfied(message_text: str, target_name: str) -> bool:
    """True only if the human's latest message both says 'confirm' and names the
    target agent. This is a runtime guard, not a prompt instruction."""
    if not message_text or not target_name:
        return False
    if not _CONFIRM_WORD.search(message_text):
        return False
    return target_name.lower() in message_text.lower()
