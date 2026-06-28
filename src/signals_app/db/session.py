"""Async SQLAlchemy engine + session factory.

Local mode (no DATABASE_URL): SQLite via aiosqlite at ./signals_local.db.
Cloud mode (DATABASE_URL set): any asyncpg-compatible URL (Neon, Supabase, etc.).
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from signals_app.db.models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

LOCAL_SQLITE_URL = "sqlite+aiosqlite:///./signals_local.db"


def _build_db_url(database_url: str | None) -> str:
    if database_url:
        # Neon/Postgres URLs from env are usually postgresql+asyncpg://...
        # Accept postgres:// and postgresql:// and convert to asyncpg driver.
        url = database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    return LOCAL_SQLITE_URL


async def init_db(database_url: str | None = None) -> None:
    """Create engine, session factory, and all tables.

    Safe to call multiple times — tables are created only if they don't exist.

    Args:
        database_url: Override DATABASE_URL. If None, reads from Settings.
    """
    global _engine, _session_factory

    if _engine is not None:
        return  # already initialised

    from signals_app.config import get_settings
    settings = get_settings()
    url = _build_db_url(database_url or settings.database_url)

    connect_args = {"check_same_thread": False} if "sqlite" in url else {}
    _engine = create_async_engine(url, echo=False, connect_args=connect_args)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("db: initialised engine url=%s", url.split("@")[-1] if "@" in url else url)


def get_session() -> AsyncSession:
    """Return a new async session. Call init_db() first."""
    if _session_factory is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _session_factory()
