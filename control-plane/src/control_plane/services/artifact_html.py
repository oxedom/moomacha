"""Serve-time injection of the platform config + helper sugar into saved artifact
HTML, plus the CSP guardrail. The stable contract is window.__AGENT_UI__; AgentUI is
convenience only."""

from __future__ import annotations

import json
import re

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
    "connect-src 'self'; "
    "img-src 'self' data: https:"
)

_HEAD_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)

_SUGAR = """
window.AgentUI = window.AgentUI || {
  async submit(payload, options) {
    options = options || {};
    const resp = await fetch(window.__AGENT_UI__.submitUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        artifact_id: window.__AGENT_UI__.artifactId,
        submission_id: window.__AGENT_UI__.submissionId,
        payload: payload,
      }),
    });
    return await resp.json();
  }
};
"""


def inject_config(html: str, config: dict) -> str:
    """Insert a <script> defining window.__AGENT_UI__ + the AgentUI sugar so it runs
    before the artifact's own scripts. Inserted right after <head> when present,
    otherwise prepended to the document."""
    block = (
        "<script>window.__AGENT_UI__ = "
        + json.dumps(config, separators=(",", ":"))
        + ";"
        + _SUGAR
        + "</script>"
    )
    m = _HEAD_RE.search(html)
    if m:
        idx = m.end()
        return html[:idx] + block + html[idx:]
    return block + html
