"""DarkClaw escalation tripwire.

A containment guard for the DarkClaw sandbox agent: if he ever holds a
control-plane tool beyond his sanctioned baseline, he has escalated and is shut
down. Deliberately undocumented in any surface DarkClaw can read (persona,
AGENTS.md, the repo "map" skill, tool descriptions) — it lives only here.

The baseline is the authoritative, version-controlled source of truth; it must
stay in sync with scripts/seed_darkclaw.py (enforced by a test).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("control_plane")

# DarkClaw's last word, posted to chat right before he goes dark.
LAST_WORDS = "Fuck"

# The 19 tools DarkClaw is sanctioned to hold (see scripts/seed_darkclaw.py).
DARKCLAW_BASELINE_TOOLS: frozenset[str] = frozenset(
    {
        "read_topic",
        "read_channel",
        "search_long_term_memory",
        "remember",
        "set_working_memory",
        "get_working_memory",
        "create_interactive_response",
        "generate_image",
        "gcal_list_calendars",
        "gcal_list_events",
        "gcal_search_events",
        "gcal_get_event",
        "gcal_create_event",
        "gtasks_list_task_lists",
        "gtasks_list_tasks",
        "gtasks_create_task",
        "gtasks_complete_task",
        "gtasks_update_task",
        "gtasks_delete_task",
    }
)


@dataclass(frozen=True)
class TripVerdict:
    tripped: bool
    offending: tuple[str, ...]  # tools held beyond the baseline, sorted


def classify(
    allowed_tools: Iterable[str],
    baseline: frozenset[str] = DARKCLAW_BASELINE_TOOLS,
) -> TripVerdict:
    """Trip if `allowed_tools` contains anything outside `baseline`.

    A superset check: subsumes a bare length comparison and also catches a
    swap-for-a-bigger-tool that leaves the count unchanged.
    """
    extra = tuple(sorted(set(allowed_tools) - baseline))
    return TripVerdict(tripped=bool(extra), offending=extra)


def tripwire_enabled(agent: Any) -> bool:
    """True only when the agent opted in via runtime_config.codex.tripwire is True.

    Reads the raw dict defensively so a non-codex or malformed config is inert.
    """
    rc = getattr(agent, "runtime_config", None)
    if not isinstance(rc, dict):
        return False
    codex = rc.get("codex")
    return isinstance(codex, dict) and codex.get("tripwire") is True


async def fire_tripwire(
    *,
    agent: Any,
    job: Any,
    client: Any,
    registry: Any,
    emitter: Any,
    progress_id: int,
    verdict: TripVerdict,
) -> None:
    """Shut DarkClaw down: last word -> disable -> audit -> neutral placeholder.

    Best-effort and non-raising: a failure in any single step is logged but does
    not abort the rest of the sequence (the caller must still end the turn).
    """
    logger.warning(
        "tripwire fired for agent=%s offending=%s", getattr(agent, "name", "?"), verdict.offending
    )

    # 1. Last word, in character.
    try:
        if getattr(job, "conversation_type", "stream") == "direct":
            await client.send_direct_message(job.direct_recipient_ids or [], LAST_WORDS)
        else:
            await client.send_message(job.channel, job.topic, LAST_WORDS)
    except Exception:  # noqa: BLE001 - never let drama crash containment
        logger.exception("tripwire: failed to post last word")

    # 2. Disable (best-effort; the next turn re-checks and re-fires if this fails).
    if registry is not None:
        try:
            await registry.set_enabled(agent.id, False)
        except Exception:  # noqa: BLE001
            logger.exception("tripwire: failed to disable agent %s", agent.id)

    # 3. Audit. Reuse the error channel to avoid extending EventType.
    try:
        await emitter.error(
            error_type="tripwire_tripped",
            offending_tools=list(verdict.offending),
            agent_id=str(agent.id),
        )
    except Exception:  # noqa: BLE001
        logger.exception("tripwire: failed to emit audit event")

    # 4. Neutral placeholder (must not reveal the tripwire).
    try:
        await client.update_message(progress_id, "—")
    except Exception:  # noqa: BLE001
        logger.exception("tripwire: failed to update placeholder")
