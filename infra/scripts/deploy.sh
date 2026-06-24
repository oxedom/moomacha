#!/usr/bin/env bash
# One-shot: apply infra -> wait for Tailscale SSH -> push secrets + bootstrap.
# Requires `op signin` first. Secrets are pulled from 1Password into TF_VARs and
# the box; nothing secret is written to disk in this repo.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$HERE"

OP_VAULT="${OP_VAULT:-ControlPlane}"
export TF_VAR_hcloud_token="$(op read "op://$OP_VAULT/box-tokens/hcloud_token")"
export TF_VAR_tailscale_auth_key="$(op read "op://$OP_VAULT/box-tokens/tailscale_auth_key")"

tofu init -input=false
tofu apply -auto-approve

HOST="$(tofu output -raw tailscale_hostname)"
echo "Waiting for Tailscale SSH on $HOST ..."
for _ in $(seq 1 60); do
  if ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -o BatchMode=yes "root@$HOST" true 2>/dev/null; then
    ok=1; break
  fi
  sleep 10
done
[ "${ok:-0}" = 1 ] || { echo "Box never became reachable over Tailscale"; exit 1; }

./scripts/push-secrets.sh "$HOST"
echo "Deploy complete -> $HOST"
