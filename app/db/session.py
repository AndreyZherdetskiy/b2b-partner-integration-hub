"""Async SQLAlchemy session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings

_SESSIONMAKERS: dict[str, async_sessionmaker[AsyncSession]] = {}
_ENGINES: dict[str, AsyncEngine] = {}


def get_sessionmaker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """Build an async sessionmaker with expire_on_commit=False (no implicit lazy loads)."""
    url = settings.database_url
    cached = _SESSIONMAKERS.get(url)
    if cached is not None:
        return cached
    engine = create_async_engine(url, pool_pre_ping=False)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    _SESSIONMAKERS[url] = maker
    _ENGINES[url] = engine
    return maker


def reset_sessionmakers() -> None:
    """Clear cached sessionmakers and engines (tests only)."""
    _SESSIONMAKERS.clear()
    _ENGINES.clear()
