"""Clone the cloud control-plane DB into a local/dev DB.

This copies all app tables and re-encrypts encrypted Zulip credentials with the
target env's AGENT_FERNET_KEY. Run Alembic on the target DB before this script.

Example:
    uv run alembic upgrade head
    uv run python scripts/clone_local_db.py \
      --source-env .env \
      --target-env .env.dev \
      --target-db-url postgresql+asyncpg://control_plane:control_plane@127.0.0.1:55432/control_plane \
      --recreate-target-schema
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import delete, inspect as sa_inspect, select, text
from sqlalchemy.exc import NoResultFound

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import (
    AgentRow,
    ArchetypeRow,
    Base,
    EventRow,
    InteractiveArtifactRow,
    InteractiveSubmissionRow,
    KnowledgeArtifactRow,
    PoolBotRow,
    ScheduledJobRow,
    SessionRow,
    SkillRow,
)
from control_plane.services.crypto import SecretBox


SECRET_FIELDS = {"zulip_api_key_encrypted", "zulip_outgoing_token_encrypted"}

TABLES = [
    ("agents", AgentRow),
    ("archetypes", ArchetypeRow),
    ("pool_bots", PoolBotRow),
    ("sessions", SessionRow),
    ("scheduled_jobs", ScheduledJobRow),
    ("knowledge_artifacts", KnowledgeArtifactRow),
    ("skills", SkillRow),
    ("interactive_artifacts", InteractiveArtifactRow),
    ("interactive_submissions", InteractiveSubmissionRow),
    ("events", EventRow),
]


def _read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key.strip()] = value
    return result


def _row_dict(row: object, source_box: SecretBox, target_box: SecretBox) -> dict[str, Any]:
    mapper = sa_inspect(type(row)).mapper
    data: dict[str, Any] = {}
    for col in mapper.columns:
        value = getattr(row, col.key)
        if col.key in SECRET_FIELDS and value:
            value = target_box.encrypt(source_box.decrypt(value))
        data[col.key] = value
    return data


async def _assert_migrated(factory) -> None:  # noqa: ANN001
    async with factory() as session:
        try:
            version = (await session.execute(text("select version_num from alembic_version"))).scalar_one()
        except NoResultFound:
            raise RuntimeError("target DB has no alembic_version row; run `uv run alembic upgrade head` first") from None
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("target DB is not migrated; run `uv run alembic upgrade head` first") from exc
        if not version:
            raise RuntimeError("target DB has an empty alembic_version; run `uv run alembic upgrade head` first")


async def clone(
    *,
    source_env: Path,
    target_env: Path,
    target_db_url: str,
    clear_target: bool,
    recreate_target_schema: bool,
) -> None:
    source_values = _read_env(source_env)
    target_values = _read_env(target_env)
    source_db_url = source_values.get("NEON_DATABASE_URL")
    source_key = source_values.get("AGENT_FERNET_KEY")
    target_key = target_values.get("AGENT_FERNET_KEY")
    if not source_db_url:
        raise RuntimeError(f"{source_env} does not define NEON_DATABASE_URL")
    if not source_key:
        raise RuntimeError(f"{source_env} does not define AGENT_FERNET_KEY")
    if not target_key:
        raise RuntimeError(f"{target_env} does not define AGENT_FERNET_KEY")

    source_box = SecretBox(source_key)
    target_box = SecretBox(target_key)

    source_factory, source_engine = build_session_factory(source_db_url)
    target_factory, target_engine = build_session_factory(target_db_url)
    await _assert_migrated(target_factory)
    if recreate_target_schema:
        async with target_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    else:
        await create_all(target_engine)

    async with source_factory() as source, target_factory() as target:
        if clear_target and not recreate_target_schema:
            for _, model in reversed(TABLES):
                await target.execute(delete(model))
            await target.commit()

        counts: dict[str, int] = {}
        for table_name, model in TABLES:
            rows = (await source.execute(select(model))).scalars().all()
            for row in rows:
                target.add(model(**_row_dict(row, source_box, target_box)))
            await target.commit()
            counts[table_name] = len(rows)

    await source_engine.dispose()
    await target_engine.dispose()

    for table_name, count in counts.items():
        print(f"{table_name}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clone cloud DB rows into local/dev DB")
    parser.add_argument("--source-env", default=".env", type=Path)
    parser.add_argument("--target-env", default=".env.dev", type=Path)
    parser.add_argument("--target-db-url", required=True)
    parser.add_argument("--clear-target", action="store_true")
    parser.add_argument(
        "--recreate-target-schema",
        action="store_true",
        help="Drop/recreate app tables from current metadata before copying. Intended for local dev DBs.",
    )
    args = parser.parse_args()
    asyncio.run(
        clone(
            source_env=args.source_env,
            target_env=args.target_env,
            target_db_url=args.target_db_url,
            clear_target=args.clear_target,
            recreate_target_schema=args.recreate_target_schema,
        )
    )


if __name__ == "__main__":
    main()
