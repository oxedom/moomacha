# whatsapp-sidecar

A minimal, **read-only** WhatsApp connector. It logs into one or more WhatsApp
accounts via [Baileys](https://github.com/WhiskeySockets/Baileys), normalizes
every incoming message, and streams them to subscribers — both in-process (an
`EventEmitter`-style bus) and over a loopback HTTP **SSE** feed.

Baileys is Node-only, which is why this lives outside the Python control-plane as
a separate process (see `docs/superpowers/specs/2026-06-04-channel-agents-frontlife-lisaops-design.md`,
§6). This package is the lean core of that design: per-account sockets +
normalize + a subscribable stream. It deliberately omits (for now) the spec's
SQLite store, deterministic classifier, and group-metadata sync — they layer on
top of the bus later.

**Read-only by construction:** there is no `sendMessage` path. The sidecar only
wires `connection.update`, `messages.upsert`, and `creds.update`.

## The message shape

Every message — DM or group, across all logged-in accounts — is normalized to:

```jsonc
{
  "account_id": "lisa",                     // which logged-in account received it
  "id": "3EB0…",                            // WhatsApp message id
  "chat_jid": "972…@s.whatsapp.net",        // group chats end in @g.us
  "is_group": false,
  "sender": "972…@s.whatsapp.net",          // the participant in groups
  "sender_name": "Alice",
  "text": "hello",                          // conversation / extended text / media caption
  "type": "text",                           // text | image | video | audio | document | other
  "timestamp": "2026-06-04T20:00:00.000Z",
  "from_me": false
}
```

## Subscribe to the stream

**Over HTTP (other processes / agents — the control-plane `whatsapp_*` tools):**

```bash
# Live feed of ALL accounts (Server-Sent Events). Optionally ?account=lisa and ?last=N backlog.
curl -N -H "Authorization: Bearer $WHATSAPP_SIDECAR_TOKEN" \
  http://127.0.0.1:8765/stream
```

```python
# Python consumer (httpx) — the pattern the control-plane uses.
import json, httpx
with httpx.stream("GET", "http://127.0.0.1:8765/stream",
                  headers={"Authorization": f"Bearer {TOKEN}"}, timeout=None) as r:
    for line in r.iter_lines():
        if line.startswith("data: "):
            msg = json.loads(line[6:])
            print(msg["account_id"], msg["sender_name"], msg["text"])
```

**In Node code (same process):**

```ts
import { MessageBus } from "whatsapp-sidecar/bus";
const unsub = bus.subscribe((m) => console.log(m.account_id, m.text));
```

## HTTP API (binds `127.0.0.1` only; bearer-gated except `/health`)

| Route | Description |
|---|---|
| `GET /health` | Liveness (no auth). |
| `GET /accounts` | Account ids, labels, connection status. |
| `GET /recent?account=&limit=` | Buffered backlog as JSON (ring buffer). |
| `GET /stream?account=&last=N` | Live SSE feed; `last=N` replays the last N first. |

The API is **never** tunneled or exposed publicly — it's a loopback IPC between
the sidecar and the control-plane.

## Configure & run

```bash
npm install
cp accounts.example.json accounts.json    # declare your accounts (id + label)
export WHATSAPP_SIDECAR_TOKEN=$(openssl rand -hex 24)
npm run serve                              # dev (tsx);  or: npm run build && npm start
```

Environment variables:

| Var | Default | Meaning |
|---|---|---|
| `WHATSAPP_SIDECAR_TOKEN` | — (**required**) | Bearer token for the HTTP API. |
| `WHATSAPP_SIDECAR_HOST` | `127.0.0.1` | Bind host. Keep it loopback. |
| `WHATSAPP_SIDECAR_PORT` | `8765` | Bind port. |
| `WHATSAPP_SIDECAR_STORE` | `./store` | Root for per-account auth state (`./store/<id>`). |
| `WHATSAPP_SIDECAR_ACCOUNTS` | `./accounts.json` | Accounts config file. |
| `WHATSAPP_SIDECAR_BUFFER` | `1000` | Ring-buffer size for stream backlog. |
| `LOG_LEVEL` | `info` | Sidecar log level. |

`accounts.json` (multi-account from day one; nothing is hardcoded):

```json
{ "accounts": [ { "id": "lisa", "label": "Lisa's business line" } ] }
```

## Pairing (one-time, per account)

On first start, an account with no saved session prints a **QR code in the logs**.
Scan it with that account's phone: **WhatsApp → Settings → Linked Devices → Link
a Device**. Credentials persist under `store/<account_id>/` (gitignored — these
are secrets). Re-pair by deleting that directory.

## Tests

```bash
npm test          # vitest: normalize, bus, http-helpers, server (incl. live SSE)
npm run typecheck
```

## Deploy

Runs as a systemd service on the box (Node is already present for codex). See
`deploy/whatsapp-sidecar.service.example`. The `store/` directory must live on a
durable host path so sessions survive restarts (the box is otherwise cattle).
