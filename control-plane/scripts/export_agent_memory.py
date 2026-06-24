"""Best-effort export of Redis Cloud Agent Memory data.

Redis Cloud Agent Memory exposes session listing, but not a full long-term-memory
dump endpoint. This script exports every listed session and deduplicates long-term
memories found by broad semantic searches. Treat the long-term section as a
snapshot, not a formally complete database dump.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from control_plane.config import Settings


DEFAULT_QUERIES = [
    "memory",
    "remember",
    "preference",
    "task",
    "project",
    "agent",
    "Claw",
    "user",
    "Zulip",
    "sandbox",
    "deploy",
    "code",
    "acme",
    "the",
    "I",
    ".",
]


class MemoryExporter:
    def __init__(self, settings: Settings, timeout: float = 30.0) -> None:
        if not (settings.agent_memory_endpoint and settings.agent_memory_store_id and settings.agent_memory_api_key):
            raise RuntimeError("source env is missing agent memory endpoint, store id, or API key")
        self._base = f"{settings.agent_memory_endpoint.rstrip('/')}/v1/stores/{settings.agent_memory_store_id}"
        self._headers = {
            "Authorization": f"Bearer {settings.agent_memory_api_key}",
            "Content-Type": "application/json",
            "accept": "application/json",
        }
        self._timeout = timeout

    async def export(self, queries: list[str]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            sessions = await self._export_sessions(client)
            long_term = await self._export_long_term(client, queries)
        return {
            "meta": {
                "exported_at": datetime.now(UTC).isoformat(),
                "long_term_export_complete": False,
                "long_term_export_note": (
                    "Redis Cloud Agent Memory does not expose a full list/dump endpoint; "
                    "these rows are deduplicated results from broad semantic searches."
                ),
                "queries": queries,
            },
            "sessions": sessions,
            "long_term_memories": long_term,
        }

    async def _export_sessions(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        listing = await self._request(client, "GET", "session-memory?limit=1000")
        items = listing.get("items", []) if isinstance(listing, dict) else []
        result: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                session_id = item.get("sessionId") or item.get("session_id") or item.get("id")
            else:
                session_id = item
            if not session_id:
                continue
            try:
                body = await self._request(client, "GET", f"session-memory/{session_id}")
            except httpx.HTTPStatusError as exc:
                body = {"export_error": str(exc)}
            result.append({"session_id": session_id, "body": body})
        return result

    async def _export_long_term(self, client: httpx.AsyncClient, queries: list[str]) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for query in queries:
            body = await self._request(
                client,
                "POST",
                "long-term-memory/search",
                json_body={"text": query, "limit": 100},
            )
            items = body.get("items", []) if isinstance(body, dict) else []
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    seen[item["id"]] = item
        return sorted(seen.values(), key=lambda item: str(item.get("id", "")))

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        response = await client.request(
            method,
            f"{self._base}/{path}",
            headers=self._headers,
            json=json_body,
        )
        response.raise_for_status()
        return response.json()


async def run(source_env: Path, output: Path, queries: list[str]) -> None:
    settings = Settings(_env_file=source_env)
    data = await MemoryExporter(settings).export(queries)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    print(f"sessions: {len(data['sessions'])}")
    print(f"long_term_memories: {len(data['long_term_memories'])}")
    print(f"written: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Redis Cloud Agent Memory best-effort snapshot")
    parser.add_argument("--source-env", default=".env", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--query", action="append", dest="queries", help="Additional broad search query")
    args = parser.parse_args()
    queries = DEFAULT_QUERIES + (args.queries or [])
    asyncio.run(run(args.source_env, args.output, queries))


if __name__ == "__main__":
    main()
