#!/usr/bin/env bash
# Runs ON THE BOX as root. Consumes files staged in /tmp/acp-stage by push-secrets.sh.
set -euo pipefail

ACP_USER="${ACP_USER:-acp}"
TUNNEL_ID="${TUNNEL_ID:?TUNNEL_ID required}"
GH_USER="${GH_USER:?GH_USER required}"
REPO_URL="${REPO_URL:?REPO_URL required}"
DOTFILES_URL="${DOTFILES_URL:-}"
STAGE=/tmp/acp-stage
REPO=/opt/agent-control-pane
HOME_DIR="/home/$ACP_USER"

# 0) Coding-agent tooling (idempotent). cloud-init installs these on first boot, but
# a box created before they were added needs them here; harmless re-run otherwise.
# npm global installs run as root (writes to /usr/lib/node_modules).
DEBIAN_FRONTEND=noninteractive apt-get install -y tmux
command -v codex >/dev/null 2>&1 || npm install -g @openai/codex
command -v claude >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code

# 1) gh auth (file-based, so it survives exec-mcp's scrubbed env via HOME) ----------
install -d -o "$ACP_USER" -g "$ACP_USER" -m 0700 "$HOME_DIR/.config" "$HOME_DIR/.config/gh"
umask 077
cat > "$HOME_DIR/.config/gh/hosts.yml" <<EOF
github.com:
    oauth_token: $(cat "$STAGE/gh_token")
    git_protocol: https
    user: $GH_USER
EOF
chown "$ACP_USER:$ACP_USER" "$HOME_DIR/.config/gh/hosts.yml"
chmod 600 "$HOME_DIR/.config/gh/hosts.yml"
sudo -u "$ACP_USER" -H gh auth setup-git

# 2) Coding-agent token (read by the cc wrapper) -----------------------------------
install -o "$ACP_USER" -g "$ACP_USER" -m 0600 "$STAGE/claude-code-token" "$HOME_DIR/.config/claude-code-token"

# 2b) Codex auth (ChatGPT mode) + config -------------------------------------------
# codex reads ~/.codex/auth.json natively from HOME; no wrapper needed. It refreshes
# its own access_token from the stored refresh_token, so a copied file keeps working.
install -d -o "$ACP_USER" -g "$ACP_USER" -m 0700 "$HOME_DIR/.codex"
install -o "$ACP_USER" -g "$ACP_USER" -m 0600 "$STAGE/codex-auth.json"   "$HOME_DIR/.codex/auth.json"
install -o "$ACP_USER" -g "$ACP_USER" -m 0644 "$STAGE/codex-config.toml" "$HOME_DIR/.codex/config.toml"

# 3) Clone the app repo (first run only) -------------------------------------------
if [ ! -d "$REPO/.git" ]; then
  sudo -u "$ACP_USER" -H git clone "$REPO_URL" "$REPO"
else
  sudo -u "$ACP_USER" -H git -C "$REPO" pull --ff-only
fi

# 3b) Optional personal dotfiles (tmux config + shell aliases for interactive SSH).
# Set DOTFILES_URL to a repo with an idempotent `init` script. Best-effort — a
# dotfiles hiccup must never abort the deploy.
if [ -n "$DOTFILES_URL" ]; then
  if [ ! -d "$HOME_DIR/dotfiles/.git" ]; then
    sudo -u "$ACP_USER" -H git clone "$DOTFILES_URL" "$HOME_DIR/dotfiles" || true
  else
    sudo -u "$ACP_USER" -H git -C "$HOME_DIR/dotfiles" pull --ff-only || true
  fi
  sudo -u "$ACP_USER" -H bash "$HOME_DIR/dotfiles/init" || true
fi

# 4) Place secrets that depend on the checkout / system dirs ------------------------
install -o "$ACP_USER" -g "$ACP_USER" -m 0600 "$STAGE/.env" "$REPO/control-plane/.env"
install -m 0600 "$STAGE/$TUNNEL_ID.json" "/etc/cloudflared/$TUNNEL_ID.json"
install -m 0644 "$STAGE/cert.pem"        "/etc/cloudflared/cert.pem"
install -m 0644 "$STAGE/config.yml"      "/etc/cloudflared/config.yml"
install -o root -g "$ACP_USER" -m 0640 "$STAGE/exec-mcp.env" "/etc/exec-mcp/exec-mcp.env"

# 5) Build the exec-mcp venv + the control-plane image -----------------------------
sudo -u "$ACP_USER" -H bash -c "cd $REPO/exec-mcp && /usr/local/bin/uv sync"
sudo -u "$ACP_USER" -H bash -c "cd $REPO/control-plane && docker compose -f docker-compose.box.yml up -d --build"

# 6) Enable services ----------------------------------------------------------------
systemctl daemon-reload
systemctl enable --now exec-mcp.service cloudflared.service control-plane-gitops.timer

# 7) Wipe staging -------------------------------------------------------------------
shred -u "$STAGE"/gh_token "$STAGE"/claude-code-token "$STAGE"/codex-auth.json "$STAGE"/.env "$STAGE"/exec-mcp.env 2>/dev/null || true
rm -rf "$STAGE"
echo "bootstrap complete"
