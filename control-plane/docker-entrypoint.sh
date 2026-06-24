#!/usr/bin/env sh
# Container entrypoint: migrate the database, then serve.
# `alembic upgrade head` is idempotent — on an already-migrated DB it is a no-op,
# and on a fresh DB it creates the full schema. This is the "first-time setup
# command" for self-hosters: it runs automatically on every boot.
set -e

echo "[entrypoint] Running database migrations (alembic upgrade head)..."
uv run alembic upgrade head

echo "[entrypoint] Starting control-plane on port ${PORT:-8000}..."
exec uv run python -m control_plane
