from control_plane.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("ZULIP_SITE", "https://example.zulipchat.com")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("OPENAI_KEY", "sk-test")
    monkeypatch.setenv("ZULIP_ADMIN_EMAIL", "admin@example.zulipchat.com")
    monkeypatch.setenv("ZULIP_ADMIN_API_KEY", "adminkey")
    monkeypatch.setenv("AGENT_FERNET_KEY", "x" * 44)

    s = Settings(_env_file=None)

    assert s.zulip_site == "https://example.zulipchat.com"
    assert s.port == 8000


def test_settings_loads_new_fields_with_defaults(monkeypatch):
    monkeypatch.setenv("ZULIP_SITE", "https://example.zulipchat.com")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("OPENAI_KEY", "sk-test")
    monkeypatch.setenv("ZULIP_ADMIN_EMAIL", "admin@example.zulipchat.com")
    monkeypatch.setenv("ZULIP_ADMIN_API_KEY", "adminkey")
    monkeypatch.setenv("AGENT_FERNET_KEY", "x" * 44)

    s = Settings(_env_file=None)

    assert s.neon_database_url.endswith("/db")
    assert s.openai_key == "sk-test"
    assert s.job_worker_count == 2
    assert s.context_default_n == 20
    assert s.public_base_url is None


def test_max_tool_calls_per_turn_defaults_to_10(monkeypatch):
    monkeypatch.setenv("ZULIP_SITE", "https://example.zulipchat.com")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("OPENAI_KEY", "sk-test")
    monkeypatch.setenv("AGENT_FERNET_KEY", "x" * 44)

    s = Settings(_env_file=None)

    assert s.max_tool_calls_per_turn == 10


def test_scheduler_config_defaults(monkeypatch):
    monkeypatch.setenv("ZULIP_SITE", "https://x.zulipchat.com")
    monkeypatch.setenv("NEON_DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("OPENAI_KEY", "sk-x")
    monkeypatch.setenv("AGENT_FERNET_KEY", "k")
    from control_plane.config import Settings

    s = Settings()
    assert s.schedule_poll_interval_seconds == 30
    assert s.scheduler_enabled is True
    assert s.schedule_misfire_grace_seconds == 3600
    assert s.schedule_max_due_per_tick == 100


def test_bastion_settings_default_to_none(monkeypatch):
    monkeypatch.setenv("ZULIP_SITE", "https://example.zulipchat.com")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("OPENAI_KEY", "sk-test")
    monkeypatch.setenv("ZULIP_ADMIN_EMAIL", "admin@example.zulipchat.com")
    monkeypatch.setenv("ZULIP_ADMIN_API_KEY", "adminkey")
    monkeypatch.setenv("AGENT_FERNET_KEY", "x" * 44)

    s = Settings(_env_file=None)

    assert s.bastion_bot_email is None
    assert s.bastion_name == "Bastion"


def test_bastion_channel_list_parses_comma_separated(monkeypatch):
    from control_plane.config import Settings

    monkeypatch.setenv("ZULIP_SITE", "https://x.zulipchat.com")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("OPENAI_KEY", "sk-x")
    monkeypatch.setenv("AGENT_FERNET_KEY", "k")
    monkeypatch.setenv("BASTION_CHANNELS", "sandbox, general ,ops")
    s = Settings(_env_file=None)
    assert s.bastion_channel_list == ["sandbox", "general", "ops"]


def test_bastion_channel_list_default_is_sandbox(monkeypatch):
    from control_plane.config import Settings

    monkeypatch.setenv("ZULIP_SITE", "https://x.zulipchat.com")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("OPENAI_KEY", "sk-x")
    monkeypatch.setenv("AGENT_FERNET_KEY", "k")
    s = Settings(_env_file=None)
    assert s.bastion_channel_list == ["sandbox"]


def _minimal_env(**over):
    base = dict(
        zulip_site="https://x.zulipchat.com",
        neon_database_url="postgresql://u:p@h/db",
        openai_key="sk-test",
        agent_fernet_key="0" * 44,
        _env_file=None,  # isolate from the live ./.env (which enables Tavily)
    )
    base.update(over)
    return base


def test_tavily_defaults_are_off_with_hosted_url():
    s = Settings(**_minimal_env())
    assert s.tavily_mcp_enabled is False
    assert s.tavily_mcp_url == "https://mcp.tavily.com/mcp/"
    assert s.tavily_api_key is None
    assert s.tavily_mcp_timeout_s == 30.0


def test_tavily_can_be_enabled():
    s = Settings(**_minimal_env(tavily_mcp_enabled=True, tavily_api_key="tvly-abc"))
    assert s.tavily_mcp_enabled is True
    assert s.tavily_api_key == "tvly-abc"
    assert s.tavily_mcp_url == "https://mcp.tavily.com/mcp/"


def test_artifact_settings_defaults():
    s = Settings(**_minimal_env())
    assert s.artifact_default_expiry_minutes == 2880
    assert s.artifact_max_expiry_minutes == 20160
    assert s.artifact_max_html_bytes == 524288
    assert s.artifact_max_payload_bytes == 65536
    assert s.artifact_summary_model == "gpt-4o-mini"


def test_image_settings_defaults():
    s = Settings(**_minimal_env())
    assert s.openai_images_enabled is False
    assert s.openai_image_model == "gpt-image-2"
    assert s.openai_image_default_size == "1024x1024"
    assert s.openai_image_default_quality == "low"
    assert s.openai_image_default_format == "png"
    assert s.openai_image_timeout_s == 120.0
    assert s.openai_image_max_bytes == 20_000_000


def test_image_settings_can_be_overridden():
    s = Settings(
        **_minimal_env(
            openai_images_enabled=True,
            openai_image_model="gpt-image-1.5",
            openai_image_default_size="1024x1536",
            openai_image_default_quality="medium",
            openai_image_default_format="webp",
            openai_image_timeout_s=42.5,
            openai_image_max_bytes=1234,
        )
    )
    assert s.openai_images_enabled is True
    assert s.openai_image_model == "gpt-image-1.5"
    assert s.openai_image_default_size == "1024x1536"
    assert s.openai_image_default_quality == "medium"
    assert s.openai_image_default_format == "webp"
    assert s.openai_image_timeout_s == 42.5
    assert s.openai_image_max_bytes == 1234


def test_context7_defaults_are_on():
    s = Settings(**_minimal_env())
    assert s.context7_enabled is True
    assert s.context7_command == ["npx", "-y", "@upstash/context7-mcp"]
    assert s.context7_timeout_s == 30.0


def test_context7_can_be_disabled():
    s = Settings(**_minimal_env(context7_enabled=False))
    assert s.context7_enabled is False


def test_observability_settings_defaults(monkeypatch):
    from control_plane.config import Settings

    s = Settings(
        zulip_site="https://z", neon_database_url="sqlite+aiosqlite://",
        openai_key="k", agent_fernet_key="f",
    )
    assert s.otel_enabled is False
    assert s.otel_exporter_otlp_endpoint is None
    assert s.sentry_dsn is None
