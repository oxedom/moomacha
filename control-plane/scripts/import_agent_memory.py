"""Import an exported Agent Memory snapshot into a local Agent Memory Server."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx


def _local_memory_record(memory: dict[str, Any]) -> dict[str, Any]:
    result = {
        "id": memory.get("id"),
        "text": memory.get("text"),
        "topics": memory.get("topics") or [],
    }
    if memory.get("namespace"):
        result["namespace"] = memory["namespace"]
    memory_type = memory.get("memory_type") or memory.get("memoryType")
    if memory_type:
        result["memory_type"] = memory_type
    if memory.get("event_date"):
        result["event_date"] = memory["event_date"]
    return {k: v for k, v in result.items() if v is not None}


async def _post_memories(client: httpx.AsyncClient, base_url: str, memories: list[dict[str, Any]]) -> None:
    if not memories:
        return
    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/v1/long-term-memory/",
            json={"memories": memories},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        if len(memories) > 1:
            for memory in memories:
                await _post_memories(client, base_url, [memory])
            return
        raise


async def import_snapshot(input_file: Path, base_url: str, batch_size: int) -> None:
    data = json.loads(input_file.read_text(encoding="utf-8"))
    memories = [_local_memory_record(m) for m in data.get("long_term_memories", [])]
    sessions = data.get("sessions", [])
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(memories), batch_size):
            await _post_memories(client, base_url, memories[i : i + batch_size])
        for session in sessions:
            session_id = session.get("session_id")
            body = session.get("body")
            if not session_id or not isinstance(body, dict):
                continue
            response = await client.put(
                f"{base_url.rstrip('/')}/v1/working-memory/{session_id}",
                json=body,
            )
            response.raise_for_status()
    print(f"long_term_memories_imported: {len(memories)}")
    print(f"sessions_imported: {len(sessions)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Agent Memory snapshot into local AMS")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(import_snapshot(args.input, args.base_url, args.batch_size))


if __name__ == "__main__":
    main()
