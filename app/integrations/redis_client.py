"""Async Redis connection pool for idempotency and rate limits."""

from __future__ import annotations

from redis.asyncio import ConnectionPool, Redis

from app.config import Settings


def create_redis_pool(settings: Settings) -> ConnectionPool:
    return ConnectionPool.from_url(settings.redis_url, decode_responses=False)


def create_redis_client(pool: ConnectionPool) -> Redis:
    return Redis(connection_pool=pool)


async def close_redis_pool(pool: ConnectionPool) -> None:
    await pool.disconnect()
