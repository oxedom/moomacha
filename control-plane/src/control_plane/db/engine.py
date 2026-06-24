from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from control_plane.db.tables import Base


def build_session_factory(
    database_url: str,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    if database_url.startswith("sqlite"):
        # Keep a single in-memory connection alive across sessions for tests.
        engine = create_async_engine(
            database_url,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        # A plain postgresql:// URL resolves to SQLAlchemy's sync psycopg2 dialect;
        # force the async asyncpg driver (the declared dependency). asyncpg also
        # rejects libpq-only query params (sslmode/channel_binding), so strip them
        # and translate an SSL requirement into asyncpg's `ssl` connect arg.
        url = make_url(database_url)
        if url.drivername in ("postgresql", "postgres"):
            url = url.set(drivername="postgresql+asyncpg")
        connect_args: dict = {}
        if url.drivername == "postgresql+asyncpg":
            query = dict(url.query)
            sslmode = query.pop("sslmode", None)
            query.pop("channel_binding", None)
            url = url.set(query=query)
            if sslmode not in (None, "disable", "allow", "prefer"):
                connect_args["ssl"] = True
        engine = create_async_engine(
            url,
            connect_args=connect_args,
            # Neon closes idle connections (and can scale to zero), so a pooled
            # connection may be dead by the time the next webhook arrives. Validate
            # each connection on checkout and recycle stale ones to reconnect
            # transparently instead of raising "connection is closed".
            pool_pre_ping=True,
            pool_recycle=300,
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


async def create_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
