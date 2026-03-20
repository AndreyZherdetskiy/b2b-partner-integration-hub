"""Notify-only webhook secret rotation reminders (no secret writes)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.domain.models.partner import Partner
from celery_app.app import celery

logger = logging.getLogger(__name__)


async def notify_secret_rotation_due(
    session: AsyncSession,
    *,
    settings: Settings,
) -> int:
    _ = settings
    result = await session.execute(select(Partner).order_by(Partner.slug.asc()))
    partners = list(result.scalars().all())
    for partner in partners:
        logger.info(
            "webhook_secret_rotation_notify",
            extra={"partner_slug": partner.slug},
        )
    return len(partners)


async def _run_notify() -> int:
    settings = get_settings()
    sessionmaker = get_sessionmaker(settings)
    async with sessionmaker() as session:
        return await notify_secret_rotation_due(session, settings=settings)


@celery.task(name="celery_app.tasks.secret_rotation.rotate_webhook_secrets")  # type: ignore[untyped-decorator]
def rotate_webhook_secrets() -> dict[str, int]:
    count = asyncio.run(_run_notify())
    return {"notified": count}
