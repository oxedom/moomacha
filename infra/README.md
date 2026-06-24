# infra — deploy your own control-plane box

OpenTofu IaC for running the control-plane on a small Hetzner Cloud VM.
Applied by a human from a laptop. The `hcloud` token and tfstate never reach
the box — this is the security substrate the self-modifying agent cannot change.

## What you need before deploying

- A **Hetzner Cloud** project + API token
- A **Tailscale** account + ephemeral auth key (admin/SSH path; no public SSH)
- A **Cloudflare** account with a domain, and a named tunnel (UUID + credentials JSON + cert)
- A **1Password** vault holding the secrets (CLI `op` signed in) — or adapt
  `scripts/push-secrets.sh` to your secret manager
- A fork/copy of this repo on GitHub (the box pulls `main` for GitOps redeploys)

### 1Password layout (defaults; override via env vars in the scripts)

| Item / document | Contents |
|---|---|
| `box-tokens` (item) | fields: `hcloud_token`, `tailscale_auth_key`, `gh_token`, `claude_code_oauth_token` |
| `control-plane-env` (document) | the control-plane `.env` (see `../control-plane/.env.example`) |
| `cloudflared-tunnel-json` (document) | the tunnel credentials JSON from `cloudflared tunnel create` |
| `cloudflared-cert` (document) | `cert.pem` from `cloudflared tunnel login` |
| `codex-auth-json` (document, optional) | `~/.codex/auth.json` from `codex login` |

Default vault name is `ControlPlane`; set `OP_VAULT` to use another.

## One-shot deploy

```bash
op signin
export TUNNEL_ID=<your-cloudflare-tunnel-uuid>
export INGRESS_HOSTNAME=agents.your-domain.com
export GH_USER=<your-github-username>
# optional: REPO_URL (defaults to github.com/$GH_USER/agent-control-plane),
#           DOTFILES_URL, OP_VAULT, OP_* document-name overrides
./scripts/deploy.sh          # tofu apply -> wait for Tailscale -> push secrets -> bootstrap
```

## Pieces

- `*.tf` — server, edge firewall (no public inbound), SSH key, cloud-init bootstrap.
- `cloud-init.yaml.tftpl` — secret-free provisioning (Docker, Tailscale, cloudflared, toolchain, systemd units).
- `files/cloudflared-config.yml.tpl` — named-tunnel ingress, locked to `/zulip/incoming`; rendered with your tunnel UUID + hostname by `push-secrets.sh`.
- `files/codex-config.toml` — non-secret Codex defaults placed at `~/.codex/config.toml`.
- `scripts/push-secrets.sh` — render secrets from 1Password, ship over Tailscale, run bootstrap.
- `scripts/bootstrap.sh` — runs on the box: place secrets, clone the app repo (+ optional dotfiles), build, enable services.
- `terraform.auto.tfvars.example` — copy to `terraform.auto.tfvars` for non-secret settings.

Recovery backdoors: Tailscale SSH (`./scripts/ssh.sh`), and the Hetzner web console (out-of-band, unkillable).

## Coding agents on the box (Claude Code + Codex; optional)

The box runs two coding agents host-side — invoked by `exec-mcp` and usable over
SSH. Auth is **subscription/account-based** (not API keys) and **cattle-safe**: the
durable credentials live in 1Password and are restored on every rebuild by
`push-secrets.sh` → `bootstrap.sh`. Nothing agent-secret is in git or cloud-init.

| Agent | Install (cloud-init) | Credential on box | Source of truth | How to run |
|---|---|---|---|---|
| **Claude Code** | `npm i -g @anthropic-ai/claude-code` | `~/.config/claude-code-token` (0600) | `op://$OP_VAULT/box-tokens/claude_code_oauth_token` | `cc` — wrapper that injects the token (`exec-mcp` scrubs env, so plain `claude` has no token) |
| **Codex** | `npm i -g @openai/codex` | `~/.codex/auth.json` (0600) + `~/.codex/config.toml` | `op://$OP_VAULT/codex-auth-json` (document) | plain `codex` — reads `auth.json` from `$HOME` directly |

### One-time interactive auth + 1Password (do this once, from the laptop)

```bash
# Claude — subscription OAuth token (long-lived, starts sk-ant-oat01-…)
claude setup-token                       # prints the token
op item edit box-tokens --vault "$OP_VAULT" 'claude_code_oauth_token[password]=<token>'

# Codex — ChatGPT login writes ~/.codex/auth.json, store the whole file as a document
codex login                              # opens browser, completes ChatGPT OAuth
op document create ~/.codex/auth.json --title codex-auth-json --vault "$OP_VAULT"
```

After secrets exist, deliver/rotate them onto the box (re-runnable, no rebuild):

```bash
TUNNEL_ID=... INGRESS_HOSTNAME=... GH_USER=... ./scripts/push-secrets.sh <tailscale-host>
```
