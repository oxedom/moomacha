"""Live probe of the Redis Agent Memory REST contract for the namespace slice.

Answers two open questions from the context-substrate spec (cannot be tested
offline):
  Q1: does POST long-term-memory/search accept a `namespace` *list* filter?
  Q2: what `event_date` format does episodic create accept?

Writes a handful of memories under an isolated, clearly-marked probe namespace
so prod data is easy to distinguish. Run from control-plane/:
    uv run python scripts/e2e_memory_probe.py
"""
from __future__ import annotations

import asyncio
import uuid

from control_plane.config import Settings
from control_plane.runtime.tools.agent_memory import AgentMemoryRest

PROBE = f"e2e-probe:{uuid.uuid4().hex[:8]}"
OTHER = f"e2e-probe-other:{uuid.uuid4().hex[:8]}"


async def main() -> None:
    s = Settings()  # reads .env
    rest = AgentMemoryRest(
        endpoint=s.agent_memory_endpoint,
        store_id=s.agent_memory_store_id,
        api_key=s.agent_memory_api_key,
        timeout=s.agent_memory_timeout_s,
    )
    print(f"endpoint={s.agent_memory_endpoint} store={s.agent_memory_store_id[:6]}…")
    print(f"probe namespace = {PROBE!r}; other = {OTHER!r}\n")

    # Seed: one memory in PROBE, one in OTHER.
    await rest.create_memories([
        {"id": str(uuid.uuid4()), "text": "PROBE_FACT alpha deploy token", "namespace": PROBE, "memoryType": "semantic"},
    ])
    await rest.create_memories([
        {"id": str(uuid.uuid4()), "text": "OTHER_FACT beta deploy token", "namespace": OTHER, "memoryType": "semantic"},
    ])
    # let any async indexing settle
    await asyncio.sleep(2.0)

    print("--- Q1: namespace LIST filter ---")
    list_res = await rest.search_memories("deploy token", limit=10, namespaces=[PROBE])
    print("search namespaces=[PROBE] ->")
    print(list_res[:1500])
    only_probe = ("PROBE_FACT" in list_res) and ("OTHER_FACT" not in list_res)
    print(f"\n=> list filter isolates PROBE only: {only_probe}\n")

    print("--- Q1b: no-namespace search (baseline) ---")
    glob = await rest.search_memories("deploy token", limit=10, namespaces=None)
    print(f"no-filter sees PROBE={'PROBE_FACT' in glob} OTHER={'OTHER_FACT' in glob}\n")

    print("--- Q2: episodic event_date formats ---")
    for label, ev in [("iso-date", "2026-05-26"), ("iso-datetime", "2026-05-26T12:00:00Z")]:
        try:
            out = await rest.create_memories([{
                "id": str(uuid.uuid4()),
                "text": f"PROBE_EPISODE {label}",
                "namespace": PROBE,
                "memoryType": "episodic",
                "event_date": ev,
            }])
            print(f"event_date={ev!r} ({label}) -> ACCEPTED: {str(out)[:200]}")
        except Exception as exc:  # noqa: BLE001
            print(f"event_date={ev!r} ({label}) -> REJECTED: {type(exc).__name__}: {str(exc)[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
