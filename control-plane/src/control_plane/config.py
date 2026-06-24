from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    zulip_site: str
    neon_database_url: str
    openai_key: str
    # Image generation tool. Reuses OPENAI_KEY; disabled unless explicitly
    # enabled so image spend is opt-in per deployment.
    openai_images_enabled: bool = False
    openai_image_model: str = "gpt-image-2"
    openai_image_default_size: str = "1024x1024"
    openai_image_default_quality: str = "low"
    openai_image_default_format: str = "png"
    openai_image_timeout_s: float = 120.0
    openai_image_max_bytes: int = 20_000_000
    # Admin creds are only needed to auto-provision bots; the manual-registration
    # path works without them, so they are optional.
    zulip_admin_email: str | None = None
    zulip_admin_api_key: str | None = None
    agent_fernet_key: str
    # Commit SHA baked into the image at build time (Dockerfile ARG GIT_SHA, fed by
    # scripts/box-redeploy.sh). Surfaced at GET /version so a running instance reports
    # what it was built from; "unknown" in local dev where nothing bakes it.
    git_sha: str = "unknown"
    job_worker_count: int = 2
    context_default_n: int = 20
    max_tool_calls_per_turn: int = 10
    # Wall-clock budget for a single agent turn. A hung runner (model call or tool
    # that never returns) is cancelled at this bound so the worker slot is freed and
    # the user gets an error instead of an indefinite spinner. The turn is dropped,
    # not replayed (agent turns are not idempotent).
    turn_timeout_seconds: float = 300.0
    # Public HTTPS base URL Zulip's webhook reaches (the tunnel host), used as the
    # outgoing-webhook payload_url. Falls back to zulip_site when unset.
    public_base_url: str | None = None
    port: int = 8000
    # Scheduler (custom poll loop). Poll cadence bounds firing latency; the grace
    # window coalesces missed fires after downtime; the per-tick cap bounds a
    # recovery burst. See docs/superpowers/specs/2026-05-24-scheduling-design.md §5.4.
    schedule_poll_interval_seconds: int = 30
    scheduler_enabled: bool = True
    schedule_misfire_grace_seconds: int = 3600
    schedule_max_due_per_tick: int = 100
    # Session lifecycle: idle sessions transition live→dormant after session_idle_seconds
    # without activity. The reaper loop polls every session_reaper_poll_seconds.
    session_idle_seconds: int = 3600
    session_reaper_poll_seconds: int = 300
    # Codex runtime: root dir for per-topic agent workspaces (each git-init'd once).
    codex_workspace_root: str = "./var/codex-workspaces"
    # Codex runtime: default sandbox mode when an agent's runtime_config doesn't set
    # one. Local dev keeps "workspace-write" (codex sandboxes itself); the box sets
    # CODEX_DEFAULT_SANDBOX_MODE=danger-full-access because codex's own sandbox can't
    # start inside Docker (the container is the isolation boundary).
    codex_default_sandbox_mode: str = "workspace-write"
    # Codex tool bridge: a loopback-only MCP server exposing an agent's allowed
    # tools to its codex turn. Bind localhost only; never tunnel it.
    codex_bridge_enabled: bool = True
    codex_bridge_host: str = "127.0.0.1"
    codex_bridge_port: int = 9110
    # Relay runtime: a debug runtime_kind that echoes the assembled turn context
    # back into chat instead of calling an LLM. Off by default — set
    # RELAY_RUNNER_ENABLED=true to register the "relay" runner (see
    # docs/superpowers/specs/2026-06-05-relay-runtime-context-echo-design.md).
    relay_runner_enabled: bool = False
    # Bastion management agent: org-specific Zulip identity, seeded on startup.
    # When unset, the bastion is not seeded and the platform still runs.
    bastion_name: str = "Bastion"
    bastion_bot_id: int | None = None
    bastion_bot_email: str | None = None
    bastion_api_key: str | None = None
    bastion_outgoing_token: str | None = None
    bastion_persona: str | None = None
    bastion_model_id: str | None = None
    # Channels the bastion is subscribed to (so humans can @-mention it there) and
    # may read. Comma-separated; an outgoing-webhook bot only fires when mentioned
    # in a stream it is subscribed to.
    bastion_channels: str = "sandbox"

    # Agent memory via Redis Cloud managed Agent Memory REST API.
    # Flagged off by default; when disabled the memory tools are not registered.
    # Creds live in .env as AGENT_MEMORY_ENDPOINT / _STORE_ID / _API_KEY.
    agent_memory_enabled: bool = False
    agent_memory_endpoint: str = "https://gcp-us-east4.memory.redis.io"
    agent_memory_store_id: str | None = None
    agent_memory_api_key: str | None = None
    agent_memory_timeout_s: float = 10.0

    # Web access via Tavily's hosted remote MCP server (streamable HTTP).
    # Flagged off by default; when disabled the Tavily tools are not registered
    # and behavior is identical. See
    # docs/superpowers/specs/2026-05-25-tavily-mcp-design.md.
    tavily_mcp_enabled: bool = False
    # Defaults to Tavily's hosted endpoint (unlike the self-hosted agent_memory_mcp_url,
    # which is None); override only if proxying.
    tavily_mcp_url: str = "https://mcp.tavily.com/mcp/"
    tavily_api_key: str | None = None
    tavily_mcp_timeout_s: float = 30.0  # advanced search / crawl / research run long

    # Google Calendar + Tasks via in-process REST (Calendar v3 + Tasks v1).
    # One installed-app OAuth client + a long-lived refresh token (minted locally
    # with the calendar + tasks scopes; consent screen published to production so
    # the refresh token does not expire). Flagged off by default; when enabled,
    # both the gcal_* and gtasks_* tools are registered and gated per-agent via
    # allowed_tools. Creds live in .env (pushed by push-secrets on the box).
    google_enabled: bool = False
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_refresh_token: str | None = None
    google_timeout_s: float = 20.0

    # Context7 docs lookup via stdio MCP (npx -y @upstash/context7-mcp).
    # Flagged on by default; set context7_enabled=false if npx is unavailable.
    context7_enabled: bool = True
    context7_command: list[str] = ["npx", "-y", "@upstash/context7-mcp"]
    context7_timeout_s: float = 30.0

    # Command execution via the isolated exec-mcp service (see
    # docs/superpowers/specs/2026-05-25-exec-mcp-design.md). Flagged off by default.
    # Authorization is enforced here even when enabled: only a can_exec agent, in an
    # exec_channels channel, invoked by an exec_users human, may run commands.
    exec_mcp_enabled: bool = False
    exec_mcp_url: str | None = None  # e.g. http://127.0.0.1:9100/sse
    exec_mcp_token: str | None = None
    exec_mcp_timeout_s: float = 65.0
    exec_channels: str = ""  # comma-separated; empty => exec allowed in no channel (fail closed)
    exec_users: str = ""  # comma-separated emails; empty => no human authorized (fail closed)
    exec_require_confirm: bool = True

    # --- Observability (slice 1) ---
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None  # e.g. http://127.0.0.1:4317 (Collector)
    otel_service_name: str = "control-plane"
    otel_traces_sampler_ratio: float = 1.0
    sentry_dsn: str | None = None  # forwarded by the Collector; here for completeness
    log_json: bool = False  # structlog JSON to stdout when true (prod); console renderer otherwise

    # Interactive response artifacts (saved executable HTML + bearer-token submit).
    # See docs/superpowers/specs/2026-05-26-interactive-response-artifacts-design.md.
    artifact_default_expiry_minutes: int = 2880  # 2 days
    artifact_max_expiry_minutes: int = 20160  # 14-day hard cap (requests clamped)
    artifact_max_html_bytes: int = 524288  # 512 KiB
    artifact_max_payload_bytes: int = 65536  # 64 KiB
    artifact_summary_model: str = "gpt-4o-mini"

    @property
    def bastion_channel_list(self) -> list[str]:
        return [c.strip() for c in self.bastion_channels.split(",") if c.strip()]

    @property
    def exec_channel_list(self) -> list[str]:
        return [c.strip() for c in self.exec_channels.split(",") if c.strip()]

    @property
    def exec_user_list(self) -> list[str]:
        return [u.strip() for u in self.exec_users.split(",") if u.strip()]
