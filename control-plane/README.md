# Control Plane — Execution Backbone (slice A)

This service turns a Zulip `@`-mention into a real reply from a DB-registered, OpenAI-backed [Strands](https://strandsagents.com) agent. A human mentions an agent's bot in a subscribed channel; Zulip posts an outgoing webhook; the control plane validates it, reacts 👍, and enqueues a job; an in-process worker assembles the recent topic as context, runs the agent, and edits a "🤔 Working on it…" placeholder into the final answer — all under the agent's own Zulip identity.

This builds on the original Zulip round-trip POC (steps 1–2). It implements build-order steps 3–5 of the master spec (`../t.md`): real agent + Postgres-backed agent definitions + context assembly. See `../docs/superpowers/specs/2026-05-23-execution-backbone-design.md` for the design and `../docs/superpowers/plans/2026-05-23-execution-backbone.md` for the build plan.

## Architecture

```
@mention → POST /zulip/incoming
  ├─ resolve agent by bot_email   (unknown → 200 no-op + event)
  ├─ validate that agent's token  (bad → 403)
  ├─ dedupe on message.id         (duplicate → 200 no-op)
  ├─ react 👍 ; write webhook_received event ; enqueue Job ; return 200
  └─ in-process worker:
       post "🤔 Working on it…" ; write job_started
       assemble context (last N topic msgs, fetched as the agent's bot)
       run Strands agent (OpenAIModel)
       edit placeholder → final reply ; write reply_posted
       (on error: edit → "⚠️ …" ; write job_failed; worker survives)
```

- **Agent definitions** live in Postgres (`agents` table); bot API keys are Fernet-encrypted at rest. Prod currently uses external Postgres, while local dev uses the bundled Docker Postgres.
- **Job queue** is an in-process `asyncio.Queue` for this slice, behind a `JobQueue` + `run_agent` seam so a later slice can swap in Postgres durability and process/container isolation without touching the webhook.
- **Audit**: worker state transitions write rows to the `events` table.
- **No agent tools** in this slice — agents are pure context responders. Permissions, approvals, scheduling, memory, skills, and multi-agent orchestration are later slices.

## Environment

Create `control-plane/.env` from `.env.example` (never commit real values). Key vars: `ZULIP_SITE`, `PUBLIC_BASE_URL` (tunnel host), `NEON_DATABASE_URL` (async, e.g. `postgresql+asyncpg://…`), `OPENAI_KEY`, `ZULIP_ADMIN_EMAIL`/`ZULIP_ADMIN_API_KEY`, `AGENT_FERNET_KEY` (generate with `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`), `JOB_WORKER_COUNT`, `CONTEXT_DEFAULT_N`.

For local Docker dev, keep the cloud/prod `.env` as the source copy and use a separate `.env.dev`:

```bash
cd control-plane
cp .env.dev.example .env.dev
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build
curl http://127.0.0.1:8000/healthz
```

`docker-compose.dev.yml` publishes local Postgres on `127.0.0.1:${POSTGRES_PORT:-55432}` for data copy scripts. Leave `NEON_DATABASE_URL` empty in `.env.dev` so the app uses the bundled Docker DB. `.env.dev` should also keep `SCHEDULER_ENABLED=false` for copied prod data, so local clones preserve schedule rows without firing them.

To clone cloud data into local Docker Postgres, first bring up `db`, run migrations against the local DB URL, then clone rows with local re-encryption:

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d db
NEON_DATABASE_URL=postgresql+asyncpg://control_plane:control_plane@127.0.0.1:${POSTGRES_PORT:-55432}/control_plane uv run alembic upgrade head
uv run python scripts/clone_local_db.py \
  --source-env .env \
  --target-env .env.dev \
  --target-db-url postgresql+asyncpg://control_plane:control_plane@127.0.0.1:${POSTGRES_PORT:-55432}/control_plane \
  --recreate-target-schema
```

Redis Agent Memory is separate from Postgres. `scripts/export_agent_memory.py` writes a best-effort snapshot: it exports all listed sessions and deduplicates long-term memories found via broad semantic searches, because Redis Cloud Agent Memory does not expose a full long-term-memory dump endpoint. To run a local memory server and import that snapshot:

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml --profile memory up -d agent-memory
uv run python scripts/import_agent_memory.py \
  --input backups/local-dev-YYYYMMDDTHHMMSSZ/agent-memory-export.json \
  --base-url http://127.0.0.1:18000
```

Set `AGENT_MEMORY_ENABLED=true` and `AGENT_MEMORY_ENDPOINT=http://agent-memory:8000` in `.env.dev` to register the memory tools against the local memory server.

## Setup & tests

```bash
cd control-plane
uv sync
uv run pytest        # full unit suite, no network
```

## Agent e2e tests

There are two e2e layers:

```bash
cd control-plane
uv run pytest tests/e2e/test_agent_turn_e2e.py -v
```

That test is deterministic and network-free, but it uses the assembled FastAPI
app, lifespan worker, SQLite tables, agent registry, webhook route, event writer,
runtime loop, and Zulip/OpenAI adapter boundaries.

For a real Zulip/OpenAI run, start the control plane with a public webhook tunnel,
configure the bot, set the optional live e2e vars from `.env.example`, then run:

```bash
cd control-plane
RUN_LIVE_AGENT_E2E=1 uv run pytest tests/e2e/test_live_zulip_agent_e2e.py -v
```

The live test registers/reuses the configured bot-backed agent, posts a mention
into Zulip through the admin API, and polls Zulip until the trigger has a `+1`
reaction and the bot placeholder has become a final agent reply.

Cleanup is on by default. The live test deletes the unique Zulip topic it
created via Zulip's admin-only `delete_topic` API, and deletes the control-plane
agent row only if the test created it. It does not delete a pre-existing reused
agent row or the underlying Zulip bot.

## App wiring (integration step)

The slice-A modules (registry, queue/worker, routers, runtime) are built and unit-tested independently. Assembling them into `app.py` (routers + lifespan workers + DB engine) is documented as a drop-in reference in `../docs/superpowers/INTEGRATION-task16-app-wiring.md`. Tests build their own app via the router-factory functions, so they don't depend on that assembly.

## Headed browser goal runner

The control plane also exposes an in-process, non-durable browser worker backed by `playwright-cli`. It starts a headed named Playwright session, lets the model use `browser_*` tools one action at a time, and accepts steering while the run is active.

```bash
curl -X POST http://localhost:8000/browser-goals \
  -H 'content-type: application/json' \
  -d '{"goal":"Check that login works","url":"http://localhost:3000","max_steps":20}'

curl http://localhost:8000/browser-goals/<run-id>

curl -X POST http://localhost:8000/browser-goals/<run-id>/steer \
  -H 'content-type: application/json' \
  -d '{"message":"Use the test account instead"}'

curl -X POST http://localhost:8000/browser-goals/<run-id>/pause
curl -X POST http://localhost:8000/browser-goals/<run-id>/resume
curl -X POST http://localhost:8000/browser-goals/<run-id>/stop
```

Agents can also be granted direct browser tools by adding names such as `browser_open`, `browser_snapshot`, `browser_click`, `browser_fill`, `browser_press`, and `browser_show_annotate` to `allowed_tools`.

## Registering an agent

`POST /agents` supports two modes:

- **Manual registration (works today):** supply `zulip_bot_id`, `zulip_bot_email`, `zulip_api_key`, and `zulip_outgoing_token` (e.g. from a bot's `zuliprc`). The control plane stores them (key encrypted) and matches the email against inbound webhooks.
- **Auto-provision:** omit the creds and the control plane creates an outgoing-webhook bot via the admin API and subscribes it to `readable_channels`. Note: bot creation does not return the outgoing-webhook token, so auto-provisioned bots need that token set before their webhooks validate — resolving this cleanly is the deferred provisioning spike. Prefer manual registration for end-to-end runs for now.

Context is fetched **as the agent's own bot**, so each agent bot must be subscribed to the channels it should read (auto-provision does this for `readable_channels`).

## Tunnel & live proof

Zulip Cloud must reach the local endpoint over HTTPS, so run a tunnel pointing at `http://127.0.0.1:${PORT:-8000}` and set the bot's outgoing-webhook endpoint to `https://<tunnel-host>/zulip/incoming`. The headed live-e2e runbook (register an agent, mention it, watch the placeholder become a real reply) is in [../scripts/e2e_playwright.md](../scripts/e2e_playwright.md).
