"""Alembic environment for the control-plane.

Reuses the application's engine construction (control_plane.db.engine) so the same
URL normalisation applies — a plain ``postgresql://`` is forced to the asyncpg
driver and libpq-only params (sslmode/channel_binding) are stripped/translated.

The DB URL is taken from the ``NEON_DATABASE_URL`` env var (the same one the app
reads) when set, otherwise from ``sqlalchemy.url`` in alembic.ini. Reading the env
var directly — rather than instantiating Settings — means migrations can run
without the app's other required secrets (OPENAI_KEY, AGENT_FERNET_KEY, …).
"""

import os
from logging.config import fileConfig

from alembic import context

from control_plane.db.engine import build_session_factory
from control_plane.db.tables import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("NEON_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "No database URL: set NEON_DATABASE_URL or sqlalchemy.url in alembic.ini"
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # noqa: ANN001
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # build_session_factory normalises the URL and returns the AsyncEngine.
    _, connectable = build_session_factory(_database_url())
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    import asyncio

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
