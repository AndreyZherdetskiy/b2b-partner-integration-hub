"""Integration fixtures — real PostgreSQL at localhost:5432."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.config import get_settings
from app.main import create_app

DATABASE_URL = "postgresql+asyncpg://hub:hub@localhost:5432/hub"
REDIS_URL = "redis://localhost:6379/0"
KAFKA_BOOTSTRAP = "localhost:9092"
INTEGRATION_ADMIN_TOKEN = "test-admin-bootstrap-token-at-least-32-bytes"


@pytest.fixture(scope="session")
def fernet_key() -> str:
    return Fernet.generate_key().decode("ascii")


@pytest.fixture(scope="session")
def admin_token() -> str:
    return INTEGRATION_ADMIN_TOKEN


@pytest.fixture(scope="session")
def integration_env(fernet_key: str, admin_token: str) -> Generator[None, None, None]:
    os.environ["DATABASE_URL"] = DATABASE_URL
    os.environ["REDIS_URL"] = REDIS_URL
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = KAFKA_BOOTSTRAP
    os.environ["FERNET_KEY"] = fernet_key
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = admin_token
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(integration_env: None) -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def db_engine(integration_env: None) -> AsyncEngine:
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_partners(db_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await session.execute(
            text(
                "TRUNCATE audit_logs, dead_letters, delivery_attempts, deliveries, "
                "inbound_events, outbox_events, partner_api_keys, partner_signing_secrets, "
                "partner_endpoints, partners "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
