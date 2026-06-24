"""The session orchestration tools must register on the tool registry, and the
cattle stores must be constructible from the same session factory + SecretBox."""
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.archetype_catalog import ArchetypeCatalog
from control_plane.services.crypto import SecretBox
from control_plane.services.pool_store import PoolStore
from control_plane.services.session_store import SessionStore
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.tools.management.adapters import register_management_tools
from control_plane.tools.management.session_adapters import register_session_tools


async def test_stores_and_tools_compose():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    reg = ToolRegistry()
    register_management_tools(reg)
    register_session_tools(reg)
    names = {s["function"]["name"] for s in reg.build_schemas([], is_bastion=True)}
    assert {"create_agent", "spin_up_session", "build_archetype"} <= names

    box = SecretBox(Fernet.generate_key().decode())
    assert ArchetypeCatalog(factory) and PoolStore(factory, box) and SessionStore(factory)
    await engine.dispose()
