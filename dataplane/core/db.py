"""
Async SQLAlchemy engine/session for the FastAPI data plane (architecture
plan §2). One engine per process, created once at import time — FastAPI
apps are long-running, unlike a request-scoped Django process, so the
connection pool is meant to be shared across requests.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dataplane.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding one session per request. Repositories are
    read-only in Phase 2 (fast-KWIC), so no explicit commit/rollback
    handling is needed yet — the worker (Phase 5) is the only writer, always
    through Django's ORM, never through this session."""
    async with async_session_factory() as session:
        yield session
