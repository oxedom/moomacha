"""Load a seed JSON file into a target DB.

Generates fresh UUIDs for every row and remaps cross-table FK references
(scheduled_jobs.agent_id). Secret fields that were nulled in the dump are
inserted as empty strings to satisfy NOT NULL constraints — re-register
credentials afterwards via attach_bot.

Aborts if any target table is non-empty unless --force is passed.

Usage:
    cd control-plane
    uv run python scripts/load_seed.py seeds/prod-2026-05-25.json
    uv run python scripts/load_seed.py seeds/prod-2026-05-25.json --force
    uv run python scripts/load_seed.py seeds/prod-2026-05-25.json --db-url postgresql+asyncpg://...
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect

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


def _parse_dt(val: str | None) -> datetime | None:
    if val is None:
        return None
    return datetime.fromisoformat(val)


def _coerce(col, val: object) -> object:
    """Convert a JSON-decoded value back to the Python type the ORM expects."""
    type_name = col.type.__class__.__name__
    if val is None:
        return val
    if type_name == "Uuid":
        return uuid.UUID(val) if isinstance(val, str) else val
    if type_name in ("DateTime", "UTCDateTime"):
        return _parse_dt(val) if isinstance(val, str) else val
    return val


def prepare_row(raw: dict, model) -> dict:
    mapper = sa_inspect(model).mapper
    result = {}
    for col in mapper.columns:
        val = raw.get(col.key)
        if col.key in SECRET_FIELDS:
            # NOT NULL columns — use empty string as sentinel; re-register creds later
            result[col.key] = val if val is not None else ""
        else:
            result[col.key] = _coerce(col, val)
    return result


async def load(db_url: str, seed_file: Path, force: bool) -> None:
    data = json.loads(seed_file.read_text())

    factory, engine = build_session_factory(db_url)

    async with factory() as session:
        if not force:
            for table_name, model in TABLES:
                count = (
                    await session.execute(select(func.count()).select_from(model))
                ).scalar()
                if count and count > 0:
                    print(
                        f"ERROR: table '{table_name}' already has {count} rows. "
                        "Use --force to load anyway (will add duplicate rows)."
                    )
                    await engine.dispose()
                    sys.exit(1)

        # Map old UUID strings → fresh UUIDs, per table
        agents_map: dict[str, uuid.UUID] = {}

        # agents
        for raw in data.get("agents", []):
            old_id = raw["id"]
            new_id = uuid.uuid4()
            agents_map[old_id] = new_id
            row_data = prepare_row(raw, AgentRow)
            row_data["id"] = new_id
            session.add(AgentRow(**row_data))

        # archetypes (no cross-table FKs in the seed)
        for raw in data.get("archetypes", []):
            row_data = prepare_row(raw, ArchetypeRow)
            row_data["id"] = uuid.uuid4()
            session.add(ArchetypeRow(**row_data))

        # pool_bots — clear current_session_id (sessions not in seed)
        for raw in data.get("pool_bots", []):
            row_data = prepare_row(raw, PoolBotRow)
            row_data["id"] = uuid.uuid4()
            row_data["current_session_id"] = None
            session.add(PoolBotRow(**row_data))

        # scheduled_jobs — remap agent_id through agents_map
        skipped = 0
        for raw in data.get("scheduled_jobs", []):
            old_agent_id = raw.get("agent_id")
            new_agent_id = agents_map.get(old_agent_id) if old_agent_id else None
            if new_agent_id is None:
                print(
                    f"  WARNING: scheduled_job '{raw['id']}' references unknown "
                    f"agent_id '{old_agent_id}'; skipping"
                )
                skipped += 1
                continue
            row_data = prepare_row(raw, ScheduledJobRow)
            row_data["id"] = uuid.uuid4()
            row_data["agent_id"] = new_agent_id
            session.add(ScheduledJobRow(**row_data))

        await session.commit()

    await engine.dispose()

    for table_name, _ in TABLES:
        n = len(data.get(table_name, []))
        suffix = f" ({skipped} skipped)" if table_name == "scheduled_jobs" and skipped else ""
        print(f"  {table_name}: {n - (skipped if table_name == 'scheduled_jobs' else 0)} rows inserted{suffix}")
    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load a seed JSON file into a target DB")
    parser.add_argument("seed_file", help="Path to seed JSON file")
    parser.add_argument("--db-url", default=None, help="Override database URL")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Insert even if target tables are non-empty (will create duplicates)",
    )
    args = parser.parse_args()
    db_url = args.db_url or Settings().neon_database_url
    asyncio.run(load(db_url, Path(args.seed_file), args.force))


if __name__ == "__main__":
    main()
