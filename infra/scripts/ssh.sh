#!/usr/bin/env bash
# SSH to the box over Tailscale (the recovery backdoor). Passes through any args.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$HERE"
exec ssh -o StrictHostKeyChecking=accept-new "root@$(tofu output -raw tailscale_hostname)" "$@"
