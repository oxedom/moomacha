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

## 🍵 What this is

Moomacha is an open-source **chat-based control plane** for AI agents. Instead
of building a dashboard with dropdowns to configure your agents, you talk to
them in a chat channel. The chat *is* the control surface.

---

## 🐄 The philosophy (or: why a herd?)

Three ideas sit at the bottom of this project:

**Conversation is the configuration.** Most agent frameworks ship with a UI —
a dashboard, a graph editor, a YAML file. Moomacha doesn't. You spawn agents by
talking to the Bastion (a privileged in-chat meta-agent). You grant capabilities
the same way. The chat transcript *is* the audit log. There is no second pane
of glass to keep in sync.

**The topic is the IDE.** In Zulip, every conversation is scoped to a *topic*.
Moomacha makes the topic the unit of work: one topic gets one assembled
context, one shared memory namespace, and one ongoing collaboration between
humans and however many agents you've invited. No topic, no context. Scope is
structural, not configured.

**Coworkers, not chatbots.** A chatbot is a function. A coworker has an
identity, a working environment, things they remember, things they're allowed
to touch, and a calendar. Each Moomacha agent gets all of these — its own
Zulip bot account, persona, memory namespace, tool ACL, schedule, and (for the
Codex runtime) a per-topic git workspace.

A few more things fall out of those:

- **Async is the default, not a feature.** Agents can take a minute, schedule
  something for tomorrow, or come back next week. Chat is the one substrate
  where that's normal — not awkward.
- **Privilege is a property of the agent, not the user.** Capabilities live on
  the agent record (`is_bastion`, `can_exec`, `is_librarian`). Even an admin
  can't make an agent do more than its flags allow. The ACL is enforced at one
  chokepoint — `ToolRuntime.execute` — for every runtime.
- **Containment is structural.** A librarian gate guards shared-memory writes.
  A tripwire shuts down sandbox agents that acquire tools beyond their
  baseline. The Bastion can't be invoked *by* other agents, only by humans.
- **Humans hold the highest privilege.** You configure the personas, grant the
  capabilities, and stay in the loop on anything that matters. The chat
  surface makes that observable by construction.

---

## 🍃 Why Zulip?

Zulip is v1 — the chat layer is a *rendering target*. Today the cows live in
Zulip topics; tomorrow they could graze somewhere else. But Zulip earns its
place:

- **Topics are first-class.** Slack threads are temporal; Zulip topics are
  persistent sub-channels. Context isolation falls out of the data model
  instead of being bolted on.
- **Outgoing webhooks are per-bot.** Each agent ships with its own outgoing
  token (Fernet-encrypted at rest), HMAC-validated on the way in. Slack/Discord
  webhooks are org-level — much coarser.
- **Self-hostable and async-cultural.** No instant-reply pressure. Agents can
  think.

---

## 🐮 Quickstart

The control plane lives under `control-plane/` and is driven by [`uv`](https://github.com/astral-sh/uv).

```bash
cd control-plane
uv sync
uv run pytest                      # 300+ tests, network-free (sqlite + ASGITransport)
cp .env.example .env               # fill in Zulip + Postgres + OpenAI + Fernet
uv run python -m control_plane     # serve on :8000
```

Expose `:8000` over HTTPS (a named Cloudflare tunnel works well), then point
your Zulip outgoing webhooks at `https://<your-host>/zulip/incoming`.

### Self-hosted stack (Docker)

```bash
cd control-plane
docker compose up -d        # control plane + Postgres; Alembic runs on container entry
```

### Production deploy (Hetzner, IaC)

A complete OpenTofu deployment — cheap Hetzner VM, Cloudflare tunnel ingress,
Tailscale-only admin access, GitOps redeploys on every `main` push, secrets
from 1Password — lives in [`infra/`](infra/). See [`infra/README.md`](infra/README.md)
for prerequisites and the one-shot deploy script.

---

## 🍃 How the herd thinks

A message hits the webhook, the queue grabs it, a worker leases the turn, and
the right runtime takes over. Every brain routes through the same ACL.

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
       (owned model→tool loop)  (LangGraph subagents)  (codex CLI in a git workspace)
                  └───────────────────────┼──────────────────────┘
                                          ▼
                       ToolRuntime ACL (the one chokepoint)
                                          │
                                          ▼
                       tools (memory · messages · scheduling ·
                       knowledge · web · exec · browser · HTML forms)
                                          │
                                          ▼
                         edit the 🤔 placeholder into the reply
```

### Three brains, one ACL

Each agent picks its inner runtime via `agents.runtime_kind` (default
`openai_tool_loop`) plus a `runtime_config` JSON blob.

| Runtime | Best for | What it is |
|---|---|---|
| **`openai_tool_loop`** | Fast, simple agents | A small owned Python loop: `client.chat.completions` → parse tool calls → `ToolRuntime.execute` → repeat. Deterministic, no graph. |
| **`deepagents`** | Subagent orchestration, plans | LangGraph-based; supports subagents and skills loaded from the Postgres `SkillCatalog`. |
| **`codex`** | Long-running coding work | Spawns the codex CLI in a per-topic git workspace; sandbox modes (read-only · workspace-write · danger-full-access); skills mounted as markdown into `.agents/skills/`. |

All three go through the same `ToolRuntime` ACL, so permissions are enforced
once, identically, no matter which brain is driving.

### Agents, sessions, and the pool

- **Archetypes** are reusable agent blueprints (persona, model, tools,
  knowledge artifacts), versioned and snapshotted at spawn.
- **Sessions** are disposable per-topic instances of an Archetype. They go
  `live → dormant → closed`.
- **The Pool** is a warm reservoir of pre-provisioned Zulip bot accounts.
  Sessions lease a bot, rename it, and return it when done — so spawning an
  agent doesn't pay the cost of minting a Zulip bot every time.

Identity (the Zulip bot) and capability (the Archetype) are deliberately
separated.

### The Bastion

A single privileged in-chat meta-agent. You talk to it to spawn, configure,
and grant capabilities to other agents. It's the only agent that can call the
management tools, and it can't be invoked *by* other agents — only by humans.
Even administration stays conversational.

---

## 🐄 What's in the barn

| Area | What's there |
|---|---|
| **Ingestion** | Webhook validation, per-agent outgoing-token HMAC check, `message.id` dedupe, 👍 ack, in-process job queue with N workers. |
| **Agent registry** | CRUD over agents; manual or auto-provisioned Zulip bots (create, subscribe, set webhook). Secrets Fernet-encrypted at rest. |
| **Archetypes / Sessions / Pool** | Reusable blueprints, disposable per-topic instances, recycled bot accounts. |
| **Bastion** | Privileged in-chat meta-agent for spawning and configuring the rest of the herd. |
| **Multi-runtime** | `openai_tool_loop`, `deepagents`, and `codex` behind one router and one ACL. |
| **Tools** | Memory, cross-topic messaging, scheduling, knowledge artifacts, Tavily search/crawl, `exec_mcp` command execution (with a human-confirm gate), Google Calendar/Tasks, image generation, Playwright browser goals, interactive HTML forms. |
| **Context substrate** | Tiered memory namespaces (`agent:*`, `topic:*`, `channel:*`, `workspace`, session) with a **librarian write-gate**; Postgres-backed knowledge artifacts; skills catalog. |
| **Scheduling** | Agent-callable one-shot and recurring scheduling on a custom poll-loop. |
| **Interactive artifacts** | Agents return signed, expiring, submittable single-page HTML forms via `create_interactive_response`. |
| **Containment** | Per-runtime ACL chokepoint, librarian gate, exec confirm-word, **DarkClaw tripwire** (sandbox agents shut down mid-turn if they acquire tools beyond their baseline). |
| **Observability** | OpenTelemetry spans, a durable audit event log, and a live SSE event stream (turn / LLM / tool / reasoning / error). |
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
- **An HTTPS path to `:8000`** (a Cloudflare named tunnel is the easy default).
- **An OpenAI API key** for OpenAI-backed runtimes.
- **A Fernet key** for encrypting bot credentials at rest:
  ```bash
  uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

`control-plane/.env.example` is the canonical, commented reference for every
supported setting — including image generation, worker count, turn timeout,
codex sandbox mode, the Bastion, and more. Optional MCP servers (Context7,
Tavily) go in [`.mcp.json.example`](.mcp.json.example).

---

## 🐮 Repository layout

```
control-plane/              FastAPI control plane (the heart of the system)
  src/control_plane/
    routes/                 HTTP surface: zulip_webhook, agents, pool, artifacts, browser_goals, dashboard
    services/               registry, sessions, pool, scheduler, crypto, context assembly, bastion, tripwire
    runtime/                AgentRunnerRouter + openai_loop + deepagents + codex + ToolRuntime
    tools/                  agent-callable tools (memory, messages, scheduling, web, exec, browser, gcal, …)
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

FastAPI · async SQLAlchemy 2 + asyncpg (any Postgres) · OpenAI SDK ·
LangChain / LangGraph / `deepagents` · codex CLI · Playwright · Fernet
(secrets at rest) · Alembic · OpenTelemetry · Docker Compose · Cloudflare
named tunnel · Tailscale · OpenTofu · `uv`.

---

## 🐄 Joining the herd

New cows welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved,
and [SECURITY.md](SECURITY.md) for how to report anything that smells off in the
enclosure.

## License

[FSL-1.1-ALv2](LICENSE.md) — Functional Source License: free for any use except
offering the software as a competing commercial service; each release converts
to Apache-2.0 two years after publication.
