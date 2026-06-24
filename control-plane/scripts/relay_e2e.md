# Relay runtime — live e2e check (playwright-cli + .env)

Proves the relay runtime echoes the real assembled context end-to-end through
Zulip. Confirms `context.md` §1.3 directly.

## Preconditions
1. Control-plane running with `RELAY_RUNNER_ENABLED=true` (in `.env`, then
   `uv run python -m control_plane`), OR the box restarted with the flag set.
2. A relay agent exists: pick/seed a sandbox bot and set its
   `runtime_kind="relay"` in the registry (DB). The same persona text it already
   has becomes the echoed `## system_prompt` header content.
3. Zulip web creds available (the operator is logged into the Zulip org in the
   browser the harness drives). The control-plane's own Zulip bot creds come
   from `.env` and need no manual step.

## Steps (browser-harness / playwright-cli)
1. Open Zulip web, go to the `#sandbox` (or `#testing`) stream, a fresh topic.
2. Post a message that @-mentions the relay agent and contains a unique marker,
   e.g.: `@**relaybot** relay-check-7f3a please echo`.
3. Wait for the agent's "🤔 Working on it…" placeholder to be edited into the
   reply (poll the topic / screenshot until the message changes).
4. Read the reply text.

## Assertions (the check passes iff all hold)
- The reply contains `🛰️ **relay** — context received this turn`.
- The reply contains a `## system_prompt` section.
- That section contains `## Recent conversation in` — the live
  `build_context_prompt()` header (this is the `context.md` §1.3 confirmation).
- The reply contains a `## user_message` section whose body includes the unique
  marker `relay-check-7f3a`.

## Teardown
- Set the agent's `runtime_kind` back to its original value if it was flipped.
- Optionally set `RELAY_RUNNER_ENABLED=false` again — relay is a check, not a
  standing feature.

## Driving it before relay is deployed to the box
The deployed box runs `main` and does **not** have the relay runner, and the bot's
real outgoing webhook routes to the box — so a mention typed in the Zulip UI is
handled by the box and fails closed, never reaching local relay code. To exercise
relay against the **live DB + live Zulip** without deploying, drive the pipeline
locally instead of through the real webhook (this is what the proven check did):

1. With `.env` (live Neon + live Zulip creds), flip a low-traffic test bot's
   `runtime_kind` to `relay` via `AgentRegistry.update(...)`, recording the original
   value to restore in a `finally`.
2. Re-resolve the agent from the DB, build the router with `relay_runner_enabled`
   on, and `router.select(agent)` → asserts a `RelayRunner` (gating + selection).
3. Post a trigger mention to a fresh `#testing` topic via the bot's `ZulipClient`,
   re-fetch history, run `build_context_prompt(...)` → `RunnerInput` → `runner.run`,
   and post the echo back with `send_message`.
4. Assert the four markers (relay header, `## system_prompt`, `## Recent
   conversation in`, marker in `## user_message`) and re-fetch the topic to confirm
   the echo landed. Then restore the original `runtime_kind`.

Proven 2026-06-05 against `sandbox-helper` in `#testing > relay-e2e-check` — all
assertions passed, row reverted `relay`→`codex`.

## Notes
- If the reply is truncated with `[truncated N chars]`, that is expected for very
  long personas/histories (size guard, `RELAY_MAX_CHARS`). The marker assertions
  still hold because `user_message` is shown in full in normal turns; only a
  pathologically large `user_message` triggers the final hard clamp.
- Flag-off behavior (negative check): with `RELAY_RUNNER_ENABLED=false`, mentioning
  a `runtime_kind="relay"` agent surfaces an `UnknownRuntimeKind` turn failure —
  do not treat that as a relay bug; it is the fail-closed path.
