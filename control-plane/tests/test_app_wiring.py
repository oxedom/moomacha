import httpx
from cryptography.fernet import Fernet
from httpx import ASGITransport

from control_plane.app import create_app
from control_plane.config import Settings


def _settings(**overrides) -> Settings:
    values = dict(
        _env_file=None,
        zulip_site="https://example.zulipchat.com",
        neon_database_url="sqlite+aiosqlite://",
        openai_key="sk-x",
        agent_fernet_key=Fernet.generate_key().decode(),
    )
    values.update(overrides)
    return Settings(
        **values,
    )


async def test_app_builds_and_serves_through_lifespan():
    app = create_app(_settings())
    # lifespan_context runs create_all + starts/stops the workers.
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/healthz")).json() == {"status": "ok"}
            assert (await c.get("/agents")).json() == []


def test_app_mounts_observability_route_and_shares_bus():
    from control_plane.app import create_app
    settings = _settings()
    app = create_app(settings)
    paths = {r.path for r in app.routes}
    assert "/observability/live" in paths


def test_image_tool_is_disabled_by_default():
    app = create_app(_settings())
    assert app.state.tool_registry.get("generate_image") is None


def test_image_tool_registers_when_enabled():
    app = create_app(_settings(openai_images_enabled=True))
    assert app.state.tool_registry.get("generate_image") is not None
