# How to connect a WhatsApp account to the sidecar

Step-by-step for pairing a WhatsApp number to the `whatsapp-sidecar` and reading
its message stream. This is the **read-only** monitor: it captures every incoming
(and own-outgoing) message and exposes them over a loopback HTTP stream. There is
no send path.

> **Privacy:** pairing links the sidecar as a WhatsApp "linked device" for that
> number — it then sees **all** of that account's chats, including personal ones.
> Only pair numbers whose owner has consented.

---

## 0. One-time setup

```bash
cd whatsapp-sidecar
npm install
```

**Bearer token** — every HTTP request needs it. The durable value lives in
1Password at `op://YourVault/box-tokens/whatsapp_sidecar_token`. Put it in a
local `.env` (gitignored):

```bash
cat > .env <<EOF
WHATSAPP_SIDECAR_TOKEN=$(op read op://YourVault/box-tokens/whatsapp_sidecar_token)
WHATSAPP_SIDECAR_HOST=127.0.0.1
WHATSAPP_SIDECAR_PORT=8765
EOF
```

(If you ever need a brand-new token: `openssl rand -hex 24`, then update the 1Password
field so local + box stay in sync — do **not** let it differ per environment.)

---

## 1. Declare the account

Add an entry to `accounts.json` (copy from `accounts.example.json`). The `id` is an
opaque handle used in the stream and as the auth-state directory name:

```json
{ "accounts": [ { "id": "personal", "label": "My business line" } ] }
```

Multi-account is supported — add more entries, pair each once.

---

## 2. Pair the number (pairing code — the reliable way)

**Do not bother with QR scanning** — WhatsApp rotates the terminal QR every ~20s and
the scan almost never lands in time. Use the pairing code:

```bash
set -a; . ./.env; set +a
npm run pair -- personal 15551234567      # <account_id> <phone, country code + number, digits only>
```

It prints an 8-character code, e.g. `9SA2-Z4A2`. On the phone for that number:

1. **WhatsApp → Settings → Linked Devices → Link a Device**
2. Tap **"Link with phone number instead"**
3. Enter the code.

You'll see `⟳ Finishing handshake (515)…` then `✓ Paired!`. The `515` reconnect is
**normal** — it completes registration. The session is saved to `store/<id>/`
(creds + signal keys). Re-pair by deleting that directory and running `pair` again.

> The session files in `store/<id>/` are secrets — gitignored, and on the box they
> must live on a durable path so they survive restarts.

---

## 3. Run the sidecar

```bash
set -a; . ./.env; set +a
npm run serve            # dev (tsx);  or:  npm run build && npm start
```

Check it connected:

```bash
TOKEN=$(grep TOKEN .env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/accounts
# {"accounts":[{"id":"personal","label":"...","status":"open"}]}
```

`status` should be `open`. `pairing` means it still needs a (re-)pair; `close` means a
transient disconnect (it auto-reconnects unless the number was logged out).

---

## 4. Read the stream

**Replay the recent buffer:**

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8765/recent?limit=20" | python3 -m json.tool
# optional filters: ?account=personal  &  &limit=N
```

**Live tail (Server-Sent Events):**

```bash
curl -N -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/stream
# ?account=personal to filter; ?last=N to replay N before going live
```

**From Python (the control-plane `whatsapp_*` tools use this pattern):**

```python
import json, httpx
TOKEN = "..."  # op read op://YourVault/box-tokens/whatsapp_sidecar_token
with httpx.stream("GET", "http://127.0.0.1:8765/stream",
                  headers={"Authorization": f"Bearer {TOKEN}"}, timeout=None) as r:
    for line in r.iter_lines():
        if line.startswith("data: "):
            msg = json.loads(line[6:])
            print(msg["account_id"], msg["sender_name"], msg["text"])
```

Each message:

```jsonc
{
  "account_id": "personal", "id": "AC57…", "chat_jid": "1203…@g.us",
  "is_group": true, "sender": "…@lid", "sender_name": "Jane Doe",
  "text": "Test", "type": "text",
  "timestamp": "2026-06-04T20:21:46.000Z", "from_me": false
}
```

---

## 5. Endpoints

| Route | Auth | Description |
|---|---|---|
| `GET /health` | no | Liveness. |
| `GET /accounts` | yes | Account ids, labels, connection status. |
| `GET /recent?account=&limit=` | yes | Buffered backlog as JSON. |
| `GET /stream?account=&last=N` | yes | Live SSE feed. |

Binds `127.0.0.1` only — never tunnel or expose it publicly. It's a loopback IPC for
the control-plane.

---

## 6. On the box (production) — runs in the GitOps pipeline

On the Hetzner box the sidecar is a **service in `control-plane/docker-compose.box.yml`**
(`whatsapp-sidecar`), so it deploys through the **same GitOps flow** as the control-plane:
a push to `main` → the box's `box-redeploy.sh` → `docker compose up -d --build` rebuilds
and restarts it. No manual steps per deploy.

Key facts about the box deployment:

- **Host networking** (`network_mode: host`), binds `127.0.0.1:8765`. The control-plane
  app (also host-net) reaches it at `127.0.0.1:8765` — no public exposure.
- **Durable state is bind-mounted** from the host (the image is cattle, the WhatsApp
  pairing is not): `/var/lib/whatsapp-sidecar/store` → `/app/store` and
  `/var/lib/whatsapp-sidecar/accounts.json` → `/app/accounts.json`.
- **Token** comes from `/etc/whatsapp-sidecar.env` (host-only `env_file`, readable by the
  `acp` deploy user; kept out of the repo and out of the app's `.env`). Same value as
  1Password `op://YourVault/box-tokens/whatsapp_sidecar_token`.

**One-time provisioning** on a fresh box (the parts GitOps can't do because they're
secrets/state): create `/etc/whatsapp-sidecar.env` (with `WHATSAPP_SIDECAR_TOKEN`),
`mkdir -p /var/lib/whatsapp-sidecar/store`, write `accounts.json` there, and pair each
account once. **Pairing in the container** (writes into the bind-mounted store):

```bash
cd /opt/agent-control-pane/control-plane
docker compose -f docker-compose.box.yml run --rm \
  -e WHATSAPP_SIDECAR_STORE=/app/store whatsapp-sidecar \
  node dist/pair.js <account_id> <phone_digits>
# enter the printed code on the phone, then the running service uses the saved session
```

> Only ever run **one** instance per session. If you also run it under systemd or locally
> against the same store, you'll get reason-440 `connectionReplaced` flapping.

The systemd unit (`deploy/whatsapp-sidecar.service.example`) is for **non-box / standalone**
hosts only; the box uses the compose service above.
