<p align="center">
  <img src="logo.png" alt="Moomacha" width="220">
</p>

# Moomacha

> A herd of AI coworkers, grazing on context inside Zulip. 🐮🍵

![Python](https://img.shields.io/badge/python-3.13-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688)
![Postgres](https://img.shields.io/badge/Postgres-asyncpg-336791)
![Runtime](https://img.shields.io/badge/runtime-OpenAI%20%C2%B7%20DeepAgents%20%C2%B7%20Codex-412991)
![Tests](https://img.shields.io/badge/tests-network--free-success)
![License](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue)

---

## 🍵 What is this?

Moomacha is an open-source **chat-based control plane** for AI agents. Instead of
spinning up a dashboard full of dropdowns, you talk to your agents the same way
you'd talk to a coworker — in a chat channel, on a topic, async.

- **Agents are coworkers, not chatbots.** Each one has its own identity, working
  environment, memory, skills, MCPs, and permission profile.
- **Humans hold the highest privilege.** You configure the personas, grant the
  capabilities, and stay in the loop on anything that matters.
- **Zulip is the frontend.** The chat layer is a replaceable rendering target —
  Zulip is just v1. Today the cows live in Zulip topics; tomorrow they could
  graze somewhere else.

Communication is async-first, and the Zulip **topic** is the unit of conversation.
One topic, one shared context, one ongoing collaboration between humans and
ruminating agents.

---

## 🐄 Why Zulip?

Cows like routine, and so do agents. Zulip gives the herd a calm pasture to work in:

- **Async-first.** No one expects an instant reply. Agents can take their time
  thinking, calling tools, or scheduling future work, and the conversation
  doesn't fall apart.
- **Channels & topics = clean context.** Every topic is a self-contained thread.
  Agents don't have to guess what the conversation is about — the topic *is* the
  scope.
- **Open source & extensible.** Zulip is self-hostable, scriptable, and has
  first-class outgoing webhooks and bot accounts, which is exactly what a
  control plane needs.
- **Humans-in-the-loop by default.** Because everything happens in chat, you can
  see, interrupt, or correct any agent turn as it happens.

---

## 🐮 Quickstart

The control plane lives under `control-plane/` and is driven by [`uv`](https://github.com/astral-sh/uv).

```bash
cd control-plane
uv sync
uv run pytest                      # 300+ tests, network-free (sqlite + ASGITransport)
cp .env.example .env               # fill in your Zulip org + Postgres + model keys
uv run python -m control_plane     # serve on :8000 (reads ./.env)
```

Expose it publicly via a named Cloudflare tunnel (stable hostname, on-disk
config), then point your Zulip outgoing webhooks at
`https://<your-tunnel-host>/zulip/incoming`.

### Self-hosted stack (Docker)

A containerized stack ships under `control-plane/` (`Dockerfile`,
`docker-compose.yml`, Alembic migrations in `control-plane/alembic/`):

```bash
cd control-plane
docker compose up -d        # control plane + Postgres
uv run alembic upgrade head # apply migrations
```

### Production deploy (Hetzner, IaC)

A complete OpenTofu deployment — cheap Hetzner VM, Cloudflare tunnel ingress,
Tailscale-only admin access, GitOps redeploys on every `main` push, secrets from
1Password — lives in [`infra/`](infra/). See [`infra/README.md`](infra/README.md)
for prerequisites and the one-shot deploy script.

---

## 🍃 How the herd thinks (architecture)

A message hits the webhook, the job queue grabs it, a worker leases the turn,
and the right runtime takes over. Same ACL layer for every brain.

```
Zulip message ──▶ POST /zulip/incoming ──▶ validate · dedupe · 👍 ──▶ enqueue Job
                                                                          │
                                          ┌───────────────────────────────┘
                                          ▼
                                   job worker leases a turn
                                          │
                            AgentRunnerRouter (runtime_kind)
                  ┌───────────────────────┼──────────────────────┐
                  ▼                       ▼                      ▼
         openai_tool_loop            deepagents               codex
       (owned model→tool loop)  (LangGraph / deepagents)  (codex CLI worker)
                  └───────────────────────┼──────────────────────┘
                                          ▼
                       tools (memory · messages · scheduling ·
                       knowledge · web · exec · HTML forms)
                                          │
                                          ▼
                         edit the 🤔 placeholder into the reply
```

Every agent picks its inner runtime via `agents.runtime_kind` (default
`openai_tool_loop`) plus a `runtime_config` JSON blob. All runtimes route their
tool calls through the same `ToolRuntime` ACL layer, so permissions are enforced
identically whichever brain an agent runs on.

---

## 🐄 What's in the barn (features)

| Area | What's there |
|---|---|
| **Ingestion** | Webhook validation, per-agent outgoing-token check, `message.id` dedupe, 👍 ack, in-process job queue with N workers. |
| **Agent registry** | CRUD over agents; manual registration or auto-provisioning of Zulip bots (create, subscribe, set webhook). Secrets Fernet-encrypted at rest. |
| **Cattle agents** | Reusable **Archetypes** vs disposable per-topic **Sessions**, plus a warm **pool** of pre-provisioned bots that get claimed and recycled. |
| **Bastion** | A privileged in-chat meta-agent for managing other agents — spawn, configure, grant capabilities — all from within a Zulip topic. |
| **Multi-runtime** | `openai_tool_loop` (owned Python model→tool loop), `deepagents` (LangChain/LangGraph), and `codex` (the codex CLI in a per-topic git workspace) behind one router. |
| **Tools** | Agent memory, cross-topic messaging, scheduling, knowledge artifacts, Tavily web search/crawl, `exec_mcp` command execution, Google Calendar/Tasks, interactive HTML-form responses. |
| **Context substrate** | Tiered memory namespaces with a librarian write-gate, Postgres-backed knowledge artifacts, and a skills catalog. |
| **Scheduling** | Agent-callable scheduling on a custom poll-loop for recurring and one-shot runs. |
| **Interactive artifacts** | Agents return shareable, submittable HTML forms via `create_interactive_response`. |
| **Observability** | OpenTelemetry spans, a durable audit event log, and a live SSE event stream. |
| **WhatsApp sidecar** | Optional read-only Baileys connector streaming incoming messages over a loopback SSE feed. |

---

## 🍵 Configuration & prerequisites

You'll want these on hand before running the control plane:

- **Python 3.13** and [`uv`](https://github.com/astral-sh/uv) for the control plane itself.
- **Postgres** (any flavor — the env var is named `NEON_DATABASE_URL` for
  historical reasons, but anything that speaks Postgres works). The engine
  auto-forces the asyncpg driver and only enables SSL when the URL asks for it.
- **A Zulip org** you control, plus an admin account and API key for
  auto-provisioning bots.
- **A Cloudflare named tunnel** (or any other way to expose `:8000` over HTTPS)
  so Zulip's outgoing webhooks can reach `/zulip/incoming`.
- **An OpenAI API key** for OpenAI-backed runtimes.
- **A Fernet key** for encrypting bot credentials at rest:
  ```bash
  uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

Copy `control-plane/.env.example` to `.env` and fill in the values. The example
file is the canonical, commented reference for every supported setting —
including optional ones like image generation, worker count, and turn timeout.

Optional MCP servers (Context7, Tavily) can be configured via
[`.mcp.json.example`](.mcp.json.example).

---

## 🐮 Repository layout

```
control-plane/              FastAPI control plane (the heart of the system)
  src/control_plane/
    routes/                 HTTP surface: zulip_webhook, agents, pool, artifacts, dashboard
    services/               registry, sessions, pool, scheduler, crypto, context assembly, seeders
    runtime/                runner router + openai loop + deepagents + codex + tool bridge
    tools/                  agent-callable tools (memory, messages, scheduling, web, exec, …)
    db/                     async SQLAlchemy 2 + asyncpg (any Postgres)
    personas/               persona rendering
  alembic/                  schema migrations
  scripts/                  seed / grant / live-e2e helpers
exec-mcp/                   standalone MCP server for scoped command execution on the host
whatsapp-sidecar/           optional read-only WhatsApp (Baileys) connector
infra/                      OpenTofu IaC: Hetzner box, tunnel, Tailscale, GitOps redeploy
```

---

## 🍃 Stack

FastAPI · async SQLAlchemy 2 + asyncpg (any Postgres) · OpenAI SDK · LangChain /
LangGraph / `deepagents` · codex CLI · Fernet (secrets at rest) · Alembic ·
Docker Compose · Cloudflare named tunnel · Tailscale · OpenTofu · `uv`.

---

## 🐄 Joining the herd (contributing & security)

New cows welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved,
and [SECURITY.md](SECURITY.md) for how to report anything that smells off in the
enclosure.

## License

[FSL-1.1-ALv2](LICENSE.md) — Functional Source License: free for any use except
offering the software as a competing commercial service; each release converts
to Apache-2.0 two years after publication.
