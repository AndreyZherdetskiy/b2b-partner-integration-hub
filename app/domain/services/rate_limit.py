"""Redis token-bucket rate limiting per partner (fail-open when Redis is down)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_TOKENS_SUFFIX = ":tokens"
_TS_SUFFIX = ":ts"
_KEY_PREFIX = "rl:"


def _tokens_key(partner_slug: str) -> str:
    return f"{_KEY_PREFIX}{partner_slug}{_TOKENS_SUFFIX}"


def _ts_key(partner_slug: str) -> str:
    return f"{_KEY_PREFIX}{partner_slug}{_TS_SUFFIX}"


async def allow_request(
    redis: Redis | None,
    *,
    partner_slug: str,
    rate_limit_rps: int,
    now: Callable[[], float] | None = None,
) -> bool:
    """True = allow. Redis None/error → True (fail-open). rps <= 0 → False when Redis is up."""
    if redis is None:
        logger.warning("rate_limit_redis_unavailable", extra={"partner_slug": partner_slug})
        return True

    if rate_limit_rps <= 0:
        return False

    now_fn = now if now is not None else time.time
    current_time = now_fn()
    burst = rate_limit_rps
    tokens_key = _tokens_key(partner_slug)
    ts_key = _ts_key(partner_slug)

    try:
        tokens_raw = await redis.get(tokens_key)
        ts_raw = await redis.get(ts_key)

        if tokens_raw is None or ts_raw is None:
            await redis.set(tokens_key, str(burst - 1))
            await redis.set(ts_key, str(current_time))
            return True

        # redis-py 8: GET may be bytes | str depending on decode_responses.
        tokens = float(tokens_raw.decode() if isinstance(tokens_raw, bytes) else tokens_raw)
        last_ts = float(ts_raw.decode() if isinstance(ts_raw, bytes) else ts_raw)
        elapsed = current_time - last_ts
        if elapsed > 0:
            tokens = min(burst, tokens + elapsed * rate_limit_rps)

        if tokens < 1:
            await redis.set(tokens_key, str(tokens))
            await redis.set(ts_key, str(current_time))
            return False

        tokens -= 1
        await redis.set(tokens_key, str(tokens))
        await redis.set(ts_key, str(current_time))
        return True
    except RedisError:
        logger.warning("rate_limit_redis_unavailable", extra={"partner_slug": partner_slug})
        return True
