import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from control_plane.db.engine import build_session_factory, create_all


@pytest.fixture
async def session_factory():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    yield factory
    await engine.dispose()


async def test_session_factory_opens_session(session_factory):
    async with session_factory() as session:
        assert isinstance(session, AsyncSession)
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
