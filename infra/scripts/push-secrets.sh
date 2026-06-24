#!/usr/bin/env bash
# Render secrets from 1Password and ship them to the box over Tailscale, then run
# bootstrap.sh on the box. Re-runnable for secret rotations. NOT needed for code
# redeploys (those pull source only). Requires `op signin` first.
set -euo pipefail

HOST="${1:?usage: push-secrets.sh <tailscale-host-or-ip>}"
OP_VAULT="${OP_VAULT:-ControlPlane}"
ACP_USER="${ACP_USER:-acp}"
# Your Cloudflare named-tunnel UUID and its public ingress hostname.
TUNNEL_ID="${TUNNEL_ID:?set TUNNEL_ID to your Cloudflare tunnel UUID}"
INGRESS_HOSTNAME="${INGRESS_HOSTNAME:?set INGRESS_HOSTNAME (e.g. agents.example.com)}"
# GitHub user/repo the box pulls from (GitOps); override to point at your fork.
GH_USER="${GH_USER:?set GH_USER to your GitHub username}"
REPO_URL="${REPO_URL:-https://github.com/$GH_USER/agent-control-plane.git}"
DOTFILES_URL="${DOTFILES_URL:-}"
# 1Password item/document names holding the secrets (see README).
OP_ENV_DOC="${OP_ENV_DOC:-control-plane-env}"
OP_CF_CRED_DOC="${OP_CF_CRED_DOC:-cloudflared-tunnel-json}"
OP_CF_CERT_DOC="${OP_CF_CERT_DOC:-cloudflared-cert}"
OP_CODEX_AUTH_DOC="${OP_CODEX_AUTH_DOC:-codex-auth-json}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

# --- render from 1Password ---
op document get "$OP_ENV_DOC" --vault "$OP_VAULT"              > "$work/.env"
op read   "op://$OP_VAULT/box-tokens/gh_token"                 > "$work/gh_token"
op read   "op://$OP_VAULT/box-tokens/claude_code_oauth_token"  > "$work/claude-code-token"
op document get "$OP_CF_CRED_DOC"   --vault "$OP_VAULT" --out-file "$work/$TUNNEL_ID.json"
op document get "$OP_CF_CERT_DOC"   --vault "$OP_VAULT" --out-file "$work/cert.pem"
op document get "$OP_CODEX_AUTH_DOC" --vault "$OP_VAULT" --out-file "$work/codex-auth.json"
sed -e "s/__TUNNEL_ID__/$TUNNEL_ID/g" -e "s/__INGRESS_HOSTNAME__/$INGRESS_HOSTNAME/g" \
  "$HERE/files/cloudflared-config.yml.tpl" > "$work/config.yml"
cp "$HERE/files/codex-config.toml"      "$work/codex-config.toml"

# exec-mcp server env: token must match the control-plane's exec_mcp_token in .env
exec_token="$(grep -E '^exec_mcp_token=' "$work/.env" | cut -d= -f2-)"
cat > "$work/exec-mcp.env" <<EOF
EXEC_MCP_TOKEN=$exec_token
EXEC_MCP_HOST=127.0.0.1
EXEC_MCP_PORT=9100
EXEC_MCP_REPO_DIR=/opt/agent-control-pane
EXEC_MCP_TIMEOUT_S=65
EOF

# --- ship + bootstrap ---
ssh -o StrictHostKeyChecking=accept-new "root@$HOST" 'rm -rf /tmp/acp-stage && mkdir -p /tmp/acp-stage'
scp -o StrictHostKeyChecking=accept-new \
  "$work/.env" "$work/gh_token" "$work/claude-code-token" \
  "$work/$TUNNEL_ID.json" "$work/cert.pem" "$work/config.yml" "$work/exec-mcp.env" \
  "$work/codex-auth.json" "$work/codex-config.toml" \
  "root@$HOST:/tmp/acp-stage/"
ssh "root@$HOST" "ACP_USER='$ACP_USER' TUNNEL_ID='$TUNNEL_ID' GH_USER='$GH_USER' REPO_URL='$REPO_URL' DOTFILES_URL='$DOTFILES_URL' bash -s" < "$HERE/scripts/bootstrap.sh"

echo "push-secrets: done -> $HOST"
