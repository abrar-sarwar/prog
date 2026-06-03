"""Async SQLAlchemy engine and session factory.

The engine and session factory are module-level singletons created lazily on
first use so that importing this module does not require ``DATABASE_URL`` to be
set (handy for tools that import the package without a real config, e.g. linting).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from config import load_config

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the lazily-constructed module-level async engine.

    The pool is deliberately bounded. We run against a Supabase connection
    pooler with a hard client cap (15 on the session pooler), so the default
    SQLAlchemy pool (5 + 10 overflow = up to 15) could consume the entire budget
    on its own and starve migrations/other clients. Capping at 5 + 5 leaves
    headroom; ``pool_pre_ping`` drops dead connections and ``pool_recycle``
    refreshes them before the pooler times them out.
    """
    global _engine
    if _engine is None:
        url = load_config().database_url
        if ":6543" in url:
            # Supabase transaction pooler (Supavisor, port 6543): the pooler
            # owns connection pooling, so use NullPool client-side, and disable
            # asyncpg's prepared-statement cache — prepared statements don't
            # survive transaction-mode pooling (different backend per txn).
            # ``prepared_statement_cache_size=0`` in the URL disables
            # SQLAlchemy's own asyncpg statement cache to match.
            _engine = create_async_engine(
                url,
                poolclass=NullPool,
                connect_args={"statement_cache_size": 0},
            )
        else:
            # Session pooler (port 5432) or a direct connection: a bounded
            # client-side pool that respects the session pooler's 15-client cap.
            _engine = create_async_engine(
                url,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=5,
                pool_timeout=30,
                pool_recycle=1800,
            )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the lazily-constructed module-level async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _session_factory


async def dispose_engine() -> None:
    """Dispose of the engine's connection pool. Call on bot shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
