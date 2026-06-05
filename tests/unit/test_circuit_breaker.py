"""Unit tests for per-partner Redis circuit breaker (Stage 2 Task 4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from redis.exceptions import RedisError

from app.config import Settings
from app.domain.services.circuit_breaker import (
    CircuitState,
    allow_outbound,
    get_circuit_state,
    is_open,
    record_failure,
    record_success,
)

PARTNER_SLUG = "acme-logistics"
FIXED_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, bytes] = {}
        self.fail = fail

    async def get(self, key: str) -> bytes | None:
        if self.fail:
            raise RedisError("redis unavailable")
        return self.store.get(key)

    async def set(
        self,
        key: str,
        value: str | bytes,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if self.fail:
            raise RedisError("redis unavailable")
        raw = value.encode() if isinstance(value, str) else value
        if nx and key in self.store:
            return False
        self.store[key] = raw
        return True

    async def incr(self, key: str) -> int:
        if self.fail:
            raise RedisError("redis unavailable")
        current = int(self.store.get(key, b"0").decode())
        current += 1
        self.store[key] = str(current).encode()
        return current

    async def expire(self, key: str, ttl: int) -> None:
        if self.fail:
            raise RedisError("redis unavailable")

    async def delete(self, *keys: str) -> int:
        if self.fail:
            raise RedisError("redis unavailable")
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed


def _settings(*, threshold: int = 3, window: int = 60, open_seconds: int = 300) -> Settings:
    return Settings(
        hub_circuit_failure_threshold=threshold,
        hub_circuit_window_seconds=window,
        hub_circuit_open_seconds=open_seconds,
    )


@pytest.mark.asyncio
async def test_n_minus_one_failures_stay_closed_nth_opens() -> None:
    redis = FakeRedis()
    settings = _settings(threshold=3)

    with patch("app.domain.services.circuit_breaker._utcnow", return_value=FIXED_NOW):
        assert await record_failure(redis, partner_slug=PARTNER_SLUG, settings=settings) == (
            CircuitState.CLOSED
        )
        assert await record_failure(redis, partner_slug=PARTNER_SLUG, settings=settings) == (
            CircuitState.CLOSED
        )
        state = await record_failure(redis, partner_slug=PARTNER_SLUG, settings=settings)

        assert state == CircuitState.OPEN
        assert await get_circuit_state(redis, partner_slug=PARTNER_SLUG, settings=settings) == (
            CircuitState.OPEN
        )
        assert await is_open(redis, partner_slug=PARTNER_SLUG, settings=settings) is True


@pytest.mark.asyncio
async def test_after_open_duration_allow_outbound_grants_single_probe() -> None:
    redis = FakeRedis()
    settings = _settings(open_seconds=300)
    opened_at = FIXED_NOW - timedelta(seconds=301)
    redis.store[f"cb:{PARTNER_SLUG}:state"] = CircuitState.OPEN.value.encode()
    redis.store[f"cb:{PARTNER_SLUG}:opened_at"] = opened_at.isoformat().encode()

    with patch("app.domain.services.circuit_breaker._utcnow", return_value=FIXED_NOW):
        assert await allow_outbound(redis, partner_slug=PARTNER_SLUG, settings=settings) is True
        assert await allow_outbound(redis, partner_slug=PARTNER_SLUG, settings=settings) is False

    assert await get_circuit_state(redis, partner_slug=PARTNER_SLUG, settings=settings) == (
        CircuitState.HALF_OPEN
    )


@pytest.mark.asyncio
async def test_half_open_success_closes_circuit() -> None:
    redis = FakeRedis()
    settings = _settings()
    redis.store[f"cb:{PARTNER_SLUG}:state"] = CircuitState.HALF_OPEN.value.encode()
    redis.store[f"cb:{PARTNER_SLUG}:probe"] = b"1"
    redis.store[f"cb:{PARTNER_SLUG}:failures"] = b"3"

    state = await record_success(redis, partner_slug=PARTNER_SLUG, settings=settings)

    assert state == CircuitState.CLOSED
    assert f"cb:{PARTNER_SLUG}:failures" not in redis.store
    assert f"cb:{PARTNER_SLUG}:probe" not in redis.store
    assert redis.store[f"cb:{PARTNER_SLUG}:state"] == CircuitState.CLOSED.value.encode()


@pytest.mark.asyncio
async def test_half_open_failure_reopens_circuit() -> None:
    redis = FakeRedis()
    settings = _settings()
    redis.store[f"cb:{PARTNER_SLUG}:state"] = CircuitState.HALF_OPEN.value.encode()
    redis.store[f"cb:{PARTNER_SLUG}:probe"] = b"1"

    with patch("app.domain.services.circuit_breaker._utcnow", return_value=FIXED_NOW):
        state = await record_failure(redis, partner_slug=PARTNER_SLUG, settings=settings)

    assert state == CircuitState.OPEN
    assert redis.store[f"cb:{PARTNER_SLUG}:state"] == CircuitState.OPEN.value.encode()
    assert f"cb:{PARTNER_SLUG}:probe" not in redis.store
    assert redis.store[f"cb:{PARTNER_SLUG}:opened_at"] == FIXED_NOW.isoformat().encode()


@pytest.mark.asyncio
async def test_redis_none_or_error_fail_open() -> None:
    settings = _settings()

    assert await allow_outbound(None, partner_slug=PARTNER_SLUG, settings=settings) is True
    assert await get_circuit_state(None, partner_slug=PARTNER_SLUG, settings=settings) == (
        CircuitState.CLOSED
    )
    assert await record_failure(None, partner_slug=PARTNER_SLUG, settings=settings) == (
        CircuitState.CLOSED
    )
    assert await record_success(None, partner_slug=PARTNER_SLUG, settings=settings) == (
        CircuitState.CLOSED
    )

    failing = FakeRedis(fail=True)
    assert await allow_outbound(failing, partner_slug=PARTNER_SLUG, settings=settings) is True
    assert await record_failure(failing, partner_slug=PARTNER_SLUG, settings=settings) == (
        CircuitState.CLOSED
    )


@pytest.mark.asyncio
async def test_keys_use_partner_slug_not_uuid() -> None:
    redis = FakeRedis()
    settings = _settings(threshold=1)

    with patch("app.domain.services.circuit_breaker._utcnow", return_value=FIXED_NOW):
        await record_failure(redis, partner_slug=PARTNER_SLUG, settings=settings)

    for key in redis.store:
        assert PARTNER_SLUG in key
        assert key.startswith("cb:")
        with pytest.raises(ValueError):
            uuid.UUID(key.split(":")[1])


def test_gauge_uses_partner_slug_and_state_only() -> None:
    with patch("app.domain.services.circuit_breaker.set_gauge_metric") as set_gauge:
        from app.domain.services.circuit_breaker import _set_state_metric

        _set_state_metric(PARTNER_SLUG, CircuitState.OPEN)

    set_gauge.assert_called_once_with(
        "hub_circuit_breaker_state",
        2,
        attributes={"partner_slug": PARTNER_SLUG, "state": "open"},
    )
