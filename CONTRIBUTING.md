# Contributing

Thanks for your interest! Issues and pull requests are welcome.

## Development

```bash
cd control-plane
uv sync
uv run pytest        # the full suite is network-free; it must pass
uvx ruff check .
```

`exec-mcp/` and `whatsapp-sidecar/` have their own suites (`uv run pytest` and
`npm test` respectively).

## Ground rules

- Keep the test suite network-free — anything that talks to a real Zulip org,
  database, or model API belongs behind an explicit opt-in env flag like the
  existing `RUN_LIVE_AGENT_E2E=1` tests.
- Never commit secrets, real org names, or personal data — including in test
  fixtures. Use `example.com` / `example.zulipchat.com` style placeholders.
- Match the surrounding code style; `ruff` is the arbiter for Python.

## Developer Certificate of Origin (DCO)

By contributing, you certify the [Developer Certificate of Origin](https://developercertificate.org/).
Sign off your commits with `git commit -s` (adds a `Signed-off-by:` trailer).
