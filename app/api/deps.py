"""FastAPI dependencies (database session, pagination, integrations)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Annotated

from aiokafka import AIOKafkaProducer
from fastapi import Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schemas.common import PaginationParams


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an async SQLAlchemy session bound to the app engine."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


def get_redis(request: Request) -> Redis | None:
    """Return the app Redis client (None when unavailable)."""
    return getattr(request.app.state, "redis", None)


def get_kafka_producer(request: Request) -> AIOKafkaProducer | None:
    """Return the app Kafka producer (None when unavailable)."""
    return getattr(request.app.state, "kafka_producer", None)


async def get_now() -> int:
    """Current Unix timestamp in seconds (override in tests)."""
    return int(time.time())


def pagination_params(
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=200,
            description="Maximum number of items to return (default 50, max 200).",
        ),
    ] = 50,
    offset: Annotated[
        int,
        Query(ge=0, description="Number of items to skip before returning results."),
    ] = 0,
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset)


DbSession = Annotated[AsyncSession, Depends(get_db)]
Pagination = Annotated[PaginationParams, Depends(pagination_params)]
RedisClient = Annotated[Redis | None, Depends(get_redis)]
KafkaProducer = Annotated[AIOKafkaProducer | None, Depends(get_kafka_producer)]
NowTimestamp = Annotated[int, Depends(get_now)]
