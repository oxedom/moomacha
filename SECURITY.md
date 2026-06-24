# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities. Instead, use
GitHub's private vulnerability reporting ("Report a vulnerability" under the
Security tab of this repository). You'll get an acknowledgement within a few days.

## Scope notes for self-hosters

- The control plane expects to sit behind an edge that only exposes
  `/zulip/incoming` publicly (see `infra/files/cloudflared-config.yml.tpl`);
  the management API (`/agents`, pool, artifacts) has no auth of its own yet
  and must never be internet-reachable.
- Agent credentials are Fernet-encrypted at rest (`AGENT_FERNET_KEY`); treat the
  key like a root secret.
- The `codex` runtime executes model-generated commands. Inside Docker it runs
  with `danger-full-access` (the container is the isolation boundary) — only
  let trusted users drive codex agents, and keep secrets out of its reach.
- `exec-mcp` is bearer-gated and capability-gated (`can_exec`), but it executes
  shell commands on the host by design. Grant it sparingly.
