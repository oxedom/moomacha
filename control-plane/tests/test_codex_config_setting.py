# tests/test_codex_config_setting.py
from control_plane.config import Settings

_REQUIRED = dict(
    zulip_site="https://x.zulipchat.com",
    neon_database_url="postgresql://u:p@h/db",
    openai_key="sk-test",
    agent_fernet_key="k",
)


def test_default_codex_workspace_root(monkeypatch):
    monkeypatch.delenv("CODEX_WORKSPACE_ROOT", raising=False)
    s = Settings(_env_file=None, **_REQUIRED)
    assert s.codex_workspace_root == "./var/codex-workspaces"


def test_env_overrides_codex_workspace_root(monkeypatch):
    monkeypatch.setenv("CODEX_WORKSPACE_ROOT", "/data/ws")
    s = Settings(_env_file=None, **_REQUIRED)
    assert s.codex_workspace_root == "/data/ws"


def test_default_codex_sandbox_mode(monkeypatch):
    monkeypatch.delenv("CODEX_DEFAULT_SANDBOX_MODE", raising=False)
    s = Settings(_env_file=None, **_REQUIRED)
    assert s.codex_default_sandbox_mode == "workspace-write"


def test_env_overrides_codex_sandbox_mode(monkeypatch):
    monkeypatch.setenv("CODEX_DEFAULT_SANDBOX_MODE", "danger-full-access")
    s = Settings(_env_file=None, **_REQUIRED)
    assert s.codex_default_sandbox_mode == "danger-full-access"


def test_codex_bridge_settings_defaults():
    s = Settings(_env_file=None, **_REQUIRED)
    assert s.codex_bridge_enabled is True
    assert s.codex_bridge_host == "127.0.0.1"
    assert s.codex_bridge_port == 9110
