"""Unit tests for per-partner Redis token-bucket rate limiting (Stage 2 Task 5)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from redis.exceptions import RedisError

from app.domain.services.rate_limit import allow_request

PARTNER_SLUG = "acme-logistics"
FIXED_NOW = 1_720_000_000.0


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


@pytest.mark.asyncio
async def test_burst_rejects_extra_request_at_same_timestamp() -> None:
    redis = FakeRedis()
    now = lambda: FIXED_NOW  # noqa: E731
    assert await allow_request(redis, partner_slug=PARTNER_SLUG, rate_limit_rps=2, now=now)
    assert await allow_request(redis, partner_slug=PARTNER_SLUG, rate_limit_rps=2, now=now)
    assert not await allow_request(redis, partner_slug=PARTNER_SLUG, rate_limit_rps=2, now=now)


@pytest.mark.asyncio
async def test_tokens_refill_after_elapsed_time() -> None:
    redis = FakeRedis()
    t = FIXED_NOW

    def now() -> float:
        return t

    assert await allow_request(redis, partner_slug=PARTNER_SLUG, rate_limit_rps=2, now=now)
    assert await allow_request(redis, partner_slug=PARTNER_SLUG, rate_limit_rps=2, now=now)
    assert not await allow_request(redis, partner_slug=PARTNER_SLUG, rate_limit_rps=2, now=now)

    t = FIXED_NOW + 1.0
    assert await allow_request(redis, partner_slug=PARTNER_SLUG, rate_limit_rps=2, now=now)


@pytest.mark.asyncio
async def test_redis_none_fail_open() -> None:
    with patch("app.domain.services.rate_limit.logger") as mock_logger:
        assert await allow_request(None, partner_slug=PARTNER_SLUG, rate_limit_rps=2)
        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.kwargs["extra"]["partner_slug"] == PARTNER_SLUG


@pytest.mark.asyncio
async def test_redis_error_fail_open() -> None:
    redis = FakeRedis(fail=True)
    with patch("app.domain.services.rate_limit.logger") as mock_logger:
        assert await allow_request(redis, partner_slug=PARTNER_SLUG, rate_limit_rps=2)
        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.kwargs["extra"]["partner_slug"] == PARTNER_SLUG


@pytest.mark.asyncio
async def test_keys_use_partner_slug_not_uuid() -> None:
    redis = FakeRedis()
    slug = "partner-alpha"
    partner_uuid = str(uuid.uuid4())
    await allow_request(redis, partner_slug=slug, rate_limit_rps=5)
    assert redis.store
    for key in redis.store:
        assert key.startswith("rl:")
        assert slug in key
        assert partner_uuid not in key
    assert f"rl:{slug}:tokens" in redis.store
    assert f"rl:{slug}:ts" in redis.store


@pytest.mark.asyncio
async def test_zero_rps_rejects_when_redis_up() -> None:
    redis = FakeRedis()
    assert not await allow_request(redis, partner_slug=PARTNER_SLUG, rate_limit_rps=0)
