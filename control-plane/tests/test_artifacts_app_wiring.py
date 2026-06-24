from cryptography.fernet import Fernet

from control_plane.app import create_app
from control_plane.config import Settings


def _settings():
    return Settings(
        _env_file=None,
        zulip_site="https://z.test",
        neon_database_url="sqlite+aiosqlite://",
        openai_key="x",
        agent_fernet_key=Fernet.generate_key().decode(),
    )


def test_app_registers_tool_and_mounts_artifacts_routes():
    app = create_app(_settings())
    paths = {r.path for r in app.routes}
    assert "/ui/artifacts/{artifact_id}" in paths
    assert "/ui/artifacts/{artifact_id}/submit" in paths
