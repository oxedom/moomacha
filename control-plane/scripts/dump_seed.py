"""Dump the Neon DB to a committed seed JSON file.

Secret fields (zulip_api_key_encrypted, zulip_outgoing_token_encrypted) are
stripped to null — the file is safe to commit.

Usage:
    cd control-plane
    uv run python scripts/dump_seed.py
    uv run python scripts/dump_seed.py --output seeds/my-snapshot.json
    uv run python scripts/dump_seed.py --db-url postgresql+asyncpg://...
"""

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect as sa_inspect, select

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.db.tables import AgentRow, ArchetypeRow, PoolBotRow, ScheduledJobRow

SECRET_FIELDS = {"zulip_api_key_encrypted", "zulip_outgoing_token_encrypted"}

TABLES = [
    ("agents", AgentRow),
    ("archetypes", ArchetypeRow),
    ("pool_bots", PoolBotRow),
    ("scheduled_jobs", ScheduledJobRow),
]


def _serialize(val: object) -> object:
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.isoformat()
    return val


def row_to_dict(row: object) -> dict:
    mapper = sa_inspect(type(row)).mapper
    result = {}
    for col in mapper.columns:
        if col.key in SECRET_FIELDS:
            result[col.key] = None
        else:
            result[col.key] = _serialize(getattr(row, col.key))
    return result


async def dump(db_url: str, output: Path) -> None:
    factory, engine = build_session_factory(db_url)
    data: dict = {
        "meta": {
            "dumped_at": datetime.now(timezone.utc).isoformat(),
            "tables": [name for name, _ in TABLES],
        }
    }
    async with factory() as session:
        for table_name, model in TABLES:
            rows = (await session.execute(select(model))).scalars().all()
            data[table_name] = [row_to_dict(r) for r in rows]
            print(f"  {table_name}: {len(data[table_name])} rows")

    await engine.dispose()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2))
    print(f"Written → {output}")


def main() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(description="Dump Neon DB to seed JSON")
    parser.add_argument("--db-url", default=None, help="Override database URL")
    parser.add_argument(
        "--output",
        default=f"seeds/prod-{today}.json",
        help="Output path (default: seeds/prod-YYYY-MM-DD.json)",
    )
    args = parser.parse_args()
    db_url = args.db_url or Settings().neon_database_url
    asyncio.run(dump(db_url, Path(args.output)))


if __name__ == "__main__":
    main()
