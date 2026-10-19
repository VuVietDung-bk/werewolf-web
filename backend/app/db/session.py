"""Async PostgreSQL sessions for request and WebSocket handlers."""

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL must be set")
    return value.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)


_session_factory: async_sessionmaker[AsyncSession] | None = None


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        engine = create_async_engine(database_url(), pool_pre_ping=True, pool_size=10, max_overflow=10)
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as db:
        yield db
