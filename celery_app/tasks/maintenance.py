"""Celery maintenance tasks."""

from __future__ import annotations

import logging

from celery_app.app import celery

logger = logging.getLogger(__name__)


@celery.task(name="celery_app.tasks.maintenance.purge_old_idempotency_keys")  # type: ignore[untyped-decorator]
def purge_old_idempotency_keys() -> dict[str, str]:
    # Inbound idempotency keys use Redis TTL (HUB_IDEMPOTENCY_TTL_HOURS) — TTL is SoT.
    logger.info(
        "purge_old_idempotency_keys_noop",
        extra={
            "detail": "inbound idempotency Redis TTL is source of truth; no PostgreSQL deletes",
        },
    )
    return {"status": "noop", "reason": "redis_ttl_is_source_of_truth"}
