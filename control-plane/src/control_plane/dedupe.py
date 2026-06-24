class SeenMessages:
    """POC-only in-process idempotency set keyed by Zulip message id."""

    def __init__(self) -> None:
        self._ids: set[int] = set()

    def mark(self, message_id: int) -> bool:
        """Return True when newly seen, or False for a duplicate delivery."""
        if message_id in self._ids:
            return False
        self._ids.add(message_id)
        return True
