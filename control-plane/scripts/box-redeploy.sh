#!/usr/bin/env bash
# Reconcile the box checkout to origin and rebuild+restart the box container ONLY
# when HEAD actually moved. Idempotent and safe to run both from the GitOps timer
# and from the agent (via exec-mcp) right after it pushes a self-modification.
#
# Auth: this runs as the `acp` service user, whose git is wired to the `gh`
# credential helper (`gh auth setup-git` in infra bootstrap.sh) so the private-repo
# HTTPS remote authenticates without a prompt. An interactive `git pull` as root
# over the Tailscale SSH backdoor has NO credentials and WILL fail with
# "could not read Username for 'https://github.com'" — that is expected; to deploy
# manually, trigger the supported path instead (works from root, runs as acp):
#     systemctl start control-plane-gitops.service
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/agent-control-pane}"
cd "$REPO_DIR"

# Serialize runs: the 3-min timer and an agent-triggered redeploy (right after a
# self-modifying push) can otherwise overlap, and two concurrent git ops in one
# working tree corrupt each other's checkout. Wait up to 5min for an in-flight run.
exec 9>"$REPO_DIR/.git/box-redeploy.lock"
flock -w 300 9 || { echo "box-redeploy: another run held the lock for >5min, skipping"; exit 0; }

before="$(git rev-parse HEAD)"
branch="$(git symbolic-ref --short HEAD)"

# Reconcile to origin with fetch + hard reset rather than `git pull --ff-only`.
# The box is cattle with no precious local changes, and `pull --ff-only` aborts
# permanently if the working tree is ever dirty (a stray edit or a concurrent-git
# race) — wedging EVERY future deploy. fetch + reset is self-healing and idempotent.
git fetch --prune origin
git reset --hard "origin/$branch"
after="$(git rev-parse HEAD)"

if [ "$before" = "$after" ]; then
  echo "box-redeploy: already at $after, nothing to do"
  exit 0
fi

echo "box-redeploy: $before -> $after, rebuilding"
cd control-plane
# Bake the pulled commit into the image so GET /version reports what's actually running.
GIT_SHA="$after" docker compose -f docker-compose.box.yml up -d --build
echo "box-redeploy: done"

# gitops redeploy verification: 2026-05-31T19:37:12Z
