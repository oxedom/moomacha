"""Summarize a submitted artifact payload with a cheap model, with a deterministic
JSON fallback. Used by the submit endpoint to build the visible Zulip message."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("control_plane")

_PREVIEW_CAP = 280
_SYSTEM = (
    "You summarize a form/UI submission in one short sentence for a chat message. "
    "Be concrete and neutral. No preamble, no markdown, max ~25 words."
)


def deterministic_preview(payload: Any) -> str:
    """Compact, truncated JSON preview — the fallback when the model is unavailable."""
    try:
        compact = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        compact = str(payload)
    if len(compact) > _PREVIEW_CAP:
        compact = compact[: _PREVIEW_CAP - 1] + "…"
    return compact


async def summarize_payload(
    client: Any, *, model: str, title: str, payload: Any
) -> tuple[str, str | None, str]:
    """Return (summary_text, summary_model_or_None, summary_status).

    status is 'generated' on model success, 'fallback' on any model failure.
    """
    user = f'Artifact title: "{title}". Submission payload JSON:\n{deterministic_preview(payload)}'
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
            max_tokens=80,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("empty summary")
        return text, model, "generated"
    except Exception:  # noqa: BLE001 - summarization must never block a submission
        logger.warning("Artifact summary model failed; using deterministic preview", exc_info=True)
        return deterministic_preview(payload), None, "fallback"
