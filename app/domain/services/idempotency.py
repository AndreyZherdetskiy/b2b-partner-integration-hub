"""Redis + DB idempotency helpers for inbound events."""

from __future__ import annotations

import uuid

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.inbound_event import InboundEvent

_IDEMPOTENCY_PREFIX = "inbound:idempotency:"


def idempotency_redis_key(partner_id: int, idempotency_key: str) -> str:
    return f"{_IDEMPOTENCY_PREFIX}{partner_id}:{idempotency_key}"


async def get_cached_event_id(
    redis: Redis | None,
    *,
    partner_id: int,
    idempotency_key: str,
) -> uuid.UUID | None:
    if redis is None:
        return None
    raw = await redis.get(idempotency_redis_key(partner_id, idempotency_key))
    if raw is None:
        return None
    return uuid.UUID(raw.decode() if isinstance(raw, bytes) else raw)


async def cache_event_id(
    redis: Redis | None,
    *,
    partner_id: int,
    idempotency_key: str,
    event_id: uuid.UUID,
    ttl_seconds: int,
) -> None:
    if redis is None:
        return
    await redis.set(
        idempotency_redis_key(partner_id, idempotency_key),
        str(event_id),
        ex=ttl_seconds,
    )


async def fetch_event_id_by_idempotency(
    session: AsyncSession,
    *,
    partner_id: int,
    idempotency_key: str,
) -> uuid.UUID | None:
    stmt = select(InboundEvent.id).where(
        InboundEvent.partner_id == partner_id,
        InboundEvent.idempotency_key == idempotency_key,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_inbound_event_by_idempotency(
    session: AsyncSession,
    *,
    partner_id: int,
    idempotency_key: str,
) -> InboundEvent | None:
    stmt = select(InboundEvent).where(
        InboundEvent.partner_id == partner_id,
        InboundEvent.idempotency_key == idempotency_key,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
