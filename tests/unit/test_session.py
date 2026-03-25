"""Engine configuration pins for accept-path DB round-trip work."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.session import get_sessionmaker, reset_sessionmakers
from app.main import create_app


def test_get_sessionmaker_disables_pool_pre_ping() -> None:
    reset_sessionmakers()
    with patch("app.db.session.create_async_engine") as mock_create:
        mock_create.return_value = MagicMock()
        get_sessionmaker(get_settings())
        assert mock_create.call_args.kwargs["pool_pre_ping"] is False


def test_create_app_lifespan_disables_pool_pre_ping() -> None:
    redis_pool = MagicMock()
    redis_pool.disconnect = AsyncMock()
    engine = MagicMock()
    engine.dispose = AsyncMock()
    with (
        patch("app.main.create_async_engine", return_value=engine) as mock_create,
        patch("app.main.create_redis_pool", return_value=redis_pool),
        patch("app.main.create_redis_client", return_value=MagicMock()),
        TestClient(create_app()),
    ):
        pass
    assert mock_create.call_args.kwargs["pool_pre_ping"] is False
