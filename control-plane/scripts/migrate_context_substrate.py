"""Apply the context-substrate v1 schema changes to Neon (idempotent).

create_all will NOT add columns to existing tables, so new columns are explicit
ALTERs; new tables use CREATE TABLE IF NOT EXISTS. All statements are additive
and safe to re-run. Run from control-plane/:
    uv run python scripts/migrate_context_substrate.py
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory

DDL = [
    """CREATE TABLE IF NOT EXISTS knowledge_artifacts (
        id uuid PRIMARY KEY,
        name varchar(255) UNIQUE NOT NULL,
        body text NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS skills (
        id uuid PRIMARY KEY,
        name varchar(255) UNIQUE NOT NULL,
        body text NOT NULL,
        model_era varchar(64) NOT NULL DEFAULT '',
        triggers jsonb NOT NULL DEFAULT '[]'::jsonb,
        active boolean NOT NULL DEFAULT true
    )""",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_librarian boolean NOT NULL DEFAULT false",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS knowledge_artifact_ids jsonb NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE archetypes ADD COLUMN IF NOT EXISTS knowledge_artifact_ids jsonb NOT NULL DEFAULT '[]'::jsonb",
]

VERIFY = [
    ("agents.is_librarian", "SELECT column_name FROM information_schema.columns WHERE table_name='agents' AND column_name='is_librarian'"),
    ("agents.knowledge_artifact_ids", "SELECT column_name FROM information_schema.columns WHERE table_name='agents' AND column_name='knowledge_artifact_ids'"),
    ("archetypes.knowledge_artifact_ids", "SELECT column_name FROM information_schema.columns WHERE table_name='archetypes' AND column_name='knowledge_artifact_ids'"),
    ("knowledge_artifacts table", "SELECT to_regclass('public.knowledge_artifacts')"),
    ("skills table", "SELECT to_regclass('public.skills')"),
]


async def main() -> None:
    s = Settings()
    factory, engine = build_session_factory(s.neon_database_url)
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
            print(f"applied: {stmt.split(chr(10))[0][:70]}…")
    print("\nverification:")
    ok = True
    async with engine.connect() as conn:
        for label, q in VERIFY:
            got = (await conn.execute(text(q))).scalar()
            present = got is not None
            ok &= present
            print(f"  {'✓' if present else '✗'} {label}: {got}")
    await engine.dispose()
    print("\nMIGRATION OK" if ok else "\nMIGRATION INCOMPLETE")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
