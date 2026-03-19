"""Per-partner Redis circuit breaker (ADR-005)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings
from app.observability.metrics import set_gauge_metric

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class AnalyticsCircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    UNKNOWN = "unknown"


_STATE_GAUGE_VALUES: dict[CircuitState, int] = {
    CircuitState.CLOSED: 0,
    CircuitState.HALF_OPEN: 1,
    CircuitState.OPEN: 2,
}


def _failures_key(partner_slug: str) -> str:
    return f"cb:{partner_slug}:failures"


def _state_key(partner_slug: str) -> str:
    return f"cb:{partner_slug}:state"


def _opened_at_key(partner_slug: str) -> str:
    return f"cb:{partner_slug}:opened_at"


def _probe_key(partner_slug: str) -> str:
    return f"cb:{partner_slug}:probe"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _decode(raw: bytes | str | None) -> str | None:
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else raw


def _set_state_metric(partner_slug: str, state: CircuitState) -> None:
    set_gauge_metric(
        "hub_circuit_breaker_state",
        _STATE_GAUGE_VALUES[state],
        attributes={"partner_slug": partner_slug, "state": state.value},
    )


async def get_circuit_state(
    redis: Redis | None,
    *,
    partner_slug: str,
    settings: Settings,
) -> CircuitState:
    if redis is None:
        return CircuitState.CLOSED
    try:
        state_raw = await redis.get(_state_key(partner_slug))
        if state_raw is None:
            return CircuitState.CLOSED

        state = CircuitState(_decode(state_raw) or CircuitState.CLOSED.value)
        if state != CircuitState.OPEN:
            return state

        opened_at_raw = await redis.get(_opened_at_key(partner_slug))
        if opened_at_raw is None:
            return CircuitState.OPEN

        opened_at = datetime.fromisoformat(_decode(opened_at_raw) or "")
        elapsed = (_utcnow() - opened_at).total_seconds()
        if elapsed >= settings.hub_circuit_open_seconds:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN
    except RedisError:
        logger.warning(
            "circuit_breaker_redis_error",
            extra={"partner_slug": partner_slug},
            exc_info=True,
        )
        return CircuitState.CLOSED


async def get_analytics_circuit_state(
    redis: Redis | None,
    *,
    partner_slug: str,
    settings: Settings,
) -> AnalyticsCircuitState:
    """Circuit state for analytics; unknown when Redis is unavailable."""
    if redis is None:
        return AnalyticsCircuitState.UNKNOWN
    try:
        state_raw = await redis.get(_state_key(partner_slug))
        if state_raw is None:
            return AnalyticsCircuitState.CLOSED

        state = CircuitState(_decode(state_raw) or CircuitState.CLOSED.value)
        if state != CircuitState.OPEN:
            return AnalyticsCircuitState(state.value)

        opened_at_raw = await redis.get(_opened_at_key(partner_slug))
        if opened_at_raw is None:
            return AnalyticsCircuitState.OPEN

        opened_at = datetime.fromisoformat(_decode(opened_at_raw) or "")
        elapsed = (_utcnow() - opened_at).total_seconds()
        if elapsed >= settings.hub_circuit_open_seconds:
            return AnalyticsCircuitState.HALF_OPEN
        return AnalyticsCircuitState.OPEN
    except RedisError:
        logger.warning(
            "circuit_breaker_redis_error",
            extra={"partner_slug": partner_slug},
            exc_info=True,
        )
        return AnalyticsCircuitState.UNKNOWN


async def allow_outbound(
    redis: Redis | None,
    *,
    partner_slug: str,
    settings: Settings,
) -> bool:
    if redis is None:
        return True
    try:
        state = await get_circuit_state(
            redis,
            partner_slug=partner_slug,
            settings=settings,
        )
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.OPEN:
            return False

        stored_raw = await redis.get(_state_key(partner_slug))
        stored = CircuitState(_decode(stored_raw) or CircuitState.CLOSED.value)
        if stored == CircuitState.OPEN:
            await redis.set(_state_key(partner_slug), CircuitState.HALF_OPEN.value)
            _set_state_metric(partner_slug, CircuitState.HALF_OPEN)

        acquired = await redis.set(
            _probe_key(partner_slug),
            b"1",
            nx=True,
            ex=settings.hub_circuit_open_seconds,
        )
        return bool(acquired)
    except RedisError:
        logger.warning(
            "circuit_breaker_redis_error",
            extra={"partner_slug": partner_slug},
            exc_info=True,
        )
        return True


async def record_failure(
    redis: Redis | None,
    *,
    partner_slug: str,
    settings: Settings,
) -> CircuitState:
    if redis is None:
        return CircuitState.CLOSED
    try:
        state = await get_circuit_state(
            redis,
            partner_slug=partner_slug,
            settings=settings,
        )
        if state == CircuitState.HALF_OPEN:
            now = _utcnow()
            await redis.set(_state_key(partner_slug), CircuitState.OPEN.value)
            await redis.set(_opened_at_key(partner_slug), now.isoformat())
            await redis.delete(_probe_key(partner_slug))
            _set_state_metric(partner_slug, CircuitState.OPEN)
            return CircuitState.OPEN

        count = await redis.incr(_failures_key(partner_slug))
        if count == 1:
            await redis.expire(_failures_key(partner_slug), settings.hub_circuit_window_seconds)

        if count >= settings.hub_circuit_failure_threshold:
            now = _utcnow()
            await redis.set(_state_key(partner_slug), CircuitState.OPEN.value)
            await redis.set(_opened_at_key(partner_slug), now.isoformat())
            _set_state_metric(partner_slug, CircuitState.OPEN)
            return CircuitState.OPEN

        return CircuitState.CLOSED
    except RedisError:
        logger.warning(
            "circuit_breaker_redis_error",
            extra={"partner_slug": partner_slug},
            exc_info=True,
        )
        return CircuitState.CLOSED


async def record_success(
    redis: Redis | None,
    *,
    partner_slug: str,
    settings: Settings,
) -> CircuitState:
    if redis is None:
        return CircuitState.CLOSED
    try:
        await redis.delete(
            _failures_key(partner_slug),
            _probe_key(partner_slug),
            _opened_at_key(partner_slug),
        )
        await redis.set(_state_key(partner_slug), CircuitState.CLOSED.value)
        _set_state_metric(partner_slug, CircuitState.CLOSED)
        return CircuitState.CLOSED
    except RedisError:
        logger.warning(
            "circuit_breaker_redis_error",
            extra={"partner_slug": partner_slug},
            exc_info=True,
        )
        return CircuitState.CLOSED


async def is_open(
    redis: Redis | None,
    *,
    partner_slug: str,
    settings: Settings,
) -> bool:
    return await get_circuit_state(redis, partner_slug=partner_slug, settings=settings) == (
        CircuitState.OPEN
    )
