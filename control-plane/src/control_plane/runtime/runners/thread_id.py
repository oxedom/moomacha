"""Stable LangGraph thread id for a Zulip conversation.

Scopes DeepAgents scratch files + checkpoints to the same Zulip
channel/topic (or DM recipient set) so scheduled fires accumulate context in
the same thread as mentions.
"""
from __future__ import annotations

from typing import Any

from control_plane.services.job_queue import Job


def make_thread_id(job: Job, agent: Any) -> str:
    if job.conversation_type == "direct":
        ids = ",".join(str(i) for i in sorted(job.direct_recipient_ids or []))
        return f"zulip:direct:{ids}:{agent.id}"
    return f"zulip:stream:{job.channel}:{job.topic}:{agent.id}"
