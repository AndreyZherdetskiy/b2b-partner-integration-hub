"""Scheduled replay of stale failed deliveries (spec J6)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.domain.enums import DeliveryStatus
from app.domain.models.delivery import Delivery
from app.domain.models.partner import Partner
from app.domain.services.circuit_breaker import CircuitState, get_circuit_state
from app.domain.services.rate_limit import allow_request
from app.domain.services.replay_service import replay_delivery
from app.integrations.redis_client import create_redis_client, create_redis_pool
from celery_app.app import celery

logger = logging.getLogger(__name__)

STALE_REPLAY_ACTOR = "celery-beat"
STALE_REPLAY_REASON = "scheduled stale failed replay"
STALE_AGE = timedelta(hours=1)
MAX_PER_TICK = 100


@dataclass
class StaleReplayResult:
    """Outcome counters for one scheduled replay tick."""

    replayed: int = 0
    skipped_open_circuit: int = 0
    skipped_rate_limited: int = 0
    skipped_auto_replay_disabled: int = 0


async def _fetch_stale_failed_deliveries(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int = MAX_PER_TICK,
) -> list[tuple[Delivery, Partner]]:
    cutoff = now - STALE_AGE
    stmt = (
        select(Delivery, Partner)
        .join(Partner, Delivery.partner_id == Partner.id)
        .where(
            Delivery.status == DeliveryStatus.FAILED.value,
            Delivery.updated_at <= cutoff,
            Partner.auto_replay_enabled.is_(True),
        )
        .order_by(Delivery.updated_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(delivery, partner) for delivery, partner in result.all()]


async def replay_stale_failed_deliveries(
    session: AsyncSession,
    *,
    redis: Redis | None,
    settings: Settings,
    now: datetime | None = None,
) -> StaleReplayResult:
    current_time = now or datetime.now(UTC)
    result = StaleReplayResult()
    rows = await _fetch_stale_failed_deliveries(session, now=current_time)

    for delivery, partner in rows:
        if not partner.auto_replay_enabled:
            result.skipped_auto_replay_disabled += 1
            continue

        circuit = await get_circuit_state(redis, partner_slug=partner.slug, settings=settings)
        if circuit != CircuitState.CLOSED:
            result.skipped_open_circuit += 1
            continue

        if not await allow_request(
            redis,
            partner_slug=partner.slug,
            rate_limit_rps=partner.rate_limit_rps,
        ):
            result.skipped_rate_limited += 1
            continue

        await replay_delivery(
            session,
            delivery_public_id=delivery.public_id,
            actor_id=STALE_REPLAY_ACTOR,
            reason=STALE_REPLAY_REASON,
            reset_attempt_counter=False,
            trigger="scheduled",
        )
        result.replayed += 1

    return result


async def _run_stale_replay() -> StaleReplayResult:
    settings = get_settings()
    sessionmaker = get_sessionmaker(settings)
    pool = create_redis_pool(settings)
    redis = create_redis_client(pool)
    try:
        async with sessionmaker() as session:
            return await replay_stale_failed_deliveries(
                session,
                redis=redis,
                settings=settings,
            )
    finally:
        await redis.aclose()
        await pool.disconnect()


@celery.task(name="celery_app.tasks.replay.replay_stale_failed")  # type: ignore[untyped-decorator]
def replay_stale_failed() -> dict[str, int]:
    outcome = asyncio.run(_run_stale_replay())
    logger.info(
        "replay_stale_failed_complete",
        extra={
            "replayed": outcome.replayed,
            "skipped_open_circuit": outcome.skipped_open_circuit,
            "skipped_rate_limited": outcome.skipped_rate_limited,
            "skipped_auto_replay_disabled": outcome.skipped_auto_replay_disabled,
        },
    )
    return {
        "replayed": outcome.replayed,
        "skipped_open_circuit": outcome.skipped_open_circuit,
        "skipped_rate_limited": outcome.skipped_rate_limited,
        "skipped_auto_replay_disabled": outcome.skipped_auto_replay_disabled,
    }
