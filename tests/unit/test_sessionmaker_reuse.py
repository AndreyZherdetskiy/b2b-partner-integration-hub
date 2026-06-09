"""Unit tests for process-wide SQLAlchemy sessionmaker reuse."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.db.session import get_sessionmaker, reset_sessionmakers


@pytest.mark.asyncio
async def test_get_sessionmaker_reuses_engine_for_same_url() -> None:
    reset_sessionmakers()
    settings = Settings(_env_file=None)
    a = get_sessionmaker(settings)
    b = get_sessionmaker(settings)
    assert a is b
    engine = a.kw["bind"]
    await engine.dispose()
    reset_sessionmakers()
