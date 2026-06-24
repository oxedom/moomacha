# Control-plane image. Build context is this directory (control-plane/).
#   docker build -t control-plane .
# Uses the official uv base (uv + CPython 3.12) so `uv sync` works identically
# to local dev. The entrypoint runs `alembic upgrade head` before serving, so a
# fresh database is migrated automatically on first boot.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Codex runtime needs: git (WorkspaceManager git-inits each topic workspace),
# Node 20 (the codex CLI launcher), and the codex binary itself. Pinned to the
# version proven on the host. Kept in one layer; build tools removed after use.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @openai/codex@0.135.0 \
    && npm cache clean --force \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/* \
    && git --version && node --version && codex --version

# Install deps first (cached layer) from the lockfile — reproducible, no dev deps.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# App source, migration assets, and runtime files (personas/skills/seeds/...).
# .dockerignore excludes secrets, .venv, caches, and build noise.
COPY . .

# Install the project itself into the environment.
RUN uv sync --frozen --no-dev && chmod +x docker-entrypoint.sh

# Bake the commit SHA so the running instance can report it at GET /version.
# Fed by scripts/box-redeploy.sh (and docker-compose build args); "unknown" otherwise.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

ENV PORT=8000
EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
