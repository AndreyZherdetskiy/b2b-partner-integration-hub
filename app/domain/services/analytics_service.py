"""PostgreSQL-backed analytics computations (spec §7.1.4)."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.enums import DeadLetterReason, DeliveryStatus
from app.domain.models.attempt import DeliveryAttempt
from app.domain.models.dead_letter import DeadLetter
from app.domain.models.delivery import Delivery
from app.domain.models.partner import Partner
from app.domain.services.circuit_breaker import AnalyticsCircuitState, get_analytics_circuit_state

WINDOW_HOURS = 24
_TERMINAL_STATUSES = (DeliveryStatus.DELIVERED.value, DeliveryStatus.FAILED.value)


@dataclass(frozen=True, slots=True)
class PartnerWindowStats:
    delivered: int
    failed: int
    sla_breaches: int
    sla_not_breached: int


def _window_start(now: datetime) -> datetime:
    return now - timedelta(hours=WINDOW_HOURS)


def compute_p95_latency_ms(durations: list[int]) -> int | None:
    if not durations:
        return None
    sorted_values = sorted(durations)
    index = math.ceil(0.95 * len(sorted_values)) - 1
    return sorted_values[index]


def compute_success_rate(delivered: int, failed: int) -> float | None:
    total = delivered + failed
    if total == 0:
        return None
    return delivered / total


def compute_sla_compliance_pct(not_breached: int, total: int) -> float | None:
    if total == 0:
        return None
    return 100.0 * not_breached / total


def _stats_from_terminal_rows(rows: list[tuple[str, bool]]) -> PartnerWindowStats:
    delivered = 0
    failed = 0
    sla_breaches = 0
    sla_not_breached = 0
    for status, sla_breached in rows:
        if status == DeliveryStatus.DELIVERED.value:
            delivered += 1
        elif status == DeliveryStatus.FAILED.value:
            failed += 1
        if sla_breached:
            sla_breaches += 1
        else:
            sla_not_breached += 1
    return PartnerWindowStats(
        delivered=delivered,
        failed=failed,
        sla_breaches=sla_breaches,
        sla_not_breached=sla_not_breached,
    )


async def fetch_partner_terminal_rows(
    session: AsyncSession,
    *,
    partner_id: int,
    window_start: datetime,
) -> list[tuple[str, bool]]:
    stmt = select(Delivery.status, Delivery.sla_breached).where(
        Delivery.partner_id == partner_id,
        Delivery.created_at >= window_start,
        Delivery.status.in_(_TERMINAL_STATUSES),
    )
    result = await session.execute(stmt)
    return cast(list[tuple[str, bool]], result.all())


async def fetch_partner_attempt_durations(
    session: AsyncSession,
    *,
    partner_id: int,
    window_start: datetime,
) -> list[int]:
    stmt = (
        select(DeliveryAttempt.duration_ms)
        .join(Delivery, DeliveryAttempt.delivery_id == Delivery.id)
        .where(
            Delivery.partner_id == partner_id,
            DeliveryAttempt.created_at >= window_start,
            DeliveryAttempt.duration_ms.is_not(None),
        )
    )
    result = await session.execute(stmt)
    return [int(row[0]) for row in result.all()]


async def fetch_oldest_unacked_dlq_created_at(
    session: AsyncSession,
    *,
    partner_id: int,
) -> datetime | None:
    stmt = select(func.min(DeadLetter.created_at)).where(
        DeadLetter.partner_id == partner_id,
        DeadLetter.acknowledged_at.is_(None),
        DeadLetter.reason != DeadLetterReason.MANUAL_PURGE.value,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def compute_dlq_age_seconds(oldest_created_at: datetime | None, *, now: datetime) -> int:
    if oldest_created_at is None:
        return 0
    return max(0, int((now - oldest_created_at).total_seconds()))


@dataclass(frozen=True, slots=True)
class PartnerSummaryData:
    id: uuid.UUID
    slug: str
    window_hours: int
    success_rate: float | None
    p95_latency_ms: int | None
    sla_compliance_pct: float | None
    sla_breaches: int
    circuit_state: AnalyticsCircuitState
    dlq_age_seconds: int


async def build_partner_summary(
    session: AsyncSession,
    *,
    partner: Partner,
    redis: Redis | None,
    settings: Settings,
    now: datetime,
) -> PartnerSummaryData:
    window_start = _window_start(now)
    terminal_rows = await fetch_partner_terminal_rows(
        session,
        partner_id=partner.id,
        window_start=window_start,
    )
    stats = _stats_from_terminal_rows(terminal_rows)
    durations = await fetch_partner_attempt_durations(
        session,
        partner_id=partner.id,
        window_start=window_start,
    )
    oldest_dlq = await fetch_oldest_unacked_dlq_created_at(session, partner_id=partner.id)
    circuit_state = await get_analytics_circuit_state(
        redis,
        partner_slug=partner.slug,
        settings=settings,
    )
    total_terminal = stats.delivered + stats.failed
    return PartnerSummaryData(
        id=partner.public_id,
        slug=partner.slug,
        window_hours=WINDOW_HOURS,
        success_rate=compute_success_rate(stats.delivered, stats.failed),
        p95_latency_ms=compute_p95_latency_ms(durations),
        sla_compliance_pct=compute_sla_compliance_pct(stats.sla_not_breached, total_terminal),
        sla_breaches=stats.sla_breaches,
        circuit_state=circuit_state,
        dlq_age_seconds=compute_dlq_age_seconds(oldest_dlq, now=now),
    )


@dataclass(frozen=True, slots=True)
class TopFailingPartnerData:
    id: uuid.UUID
    slug: str
    success_rate: float
    sla_breaches: int


@dataclass(frozen=True, slots=True)
class OverviewData:
    window_hours: int
    dlq_count: int
    avg_sla_compliance_pct: float | None
    top_failing_partners: list[TopFailingPartnerData]


async def fetch_dlq_backlog_count(session: AsyncSession) -> int:
    stmt = (
        select(func.count())
        .select_from(DeadLetter)
        .where(DeadLetter.reason != DeadLetterReason.MANUAL_PURGE.value)
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def fetch_overview_partner_rows(
    session: AsyncSession,
    *,
    window_start: datetime,
) -> list[tuple[uuid.UUID, str, str, bool]]:
    stmt = (
        select(Partner.public_id, Partner.slug, Delivery.status, Delivery.sla_breached)
        .join(Delivery, Delivery.partner_id == Partner.id)
        .where(
            Delivery.created_at >= window_start,
            Delivery.status.in_(_TERMINAL_STATUSES),
        )
    )
    result = await session.execute(stmt)
    return cast(list[tuple[uuid.UUID, str, str, bool]], result.all())


def _aggregate_overview_partners(
    rows: list[tuple[uuid.UUID, str, str, bool]],
) -> list[TopFailingPartnerData]:
    grouped: dict[uuid.UUID, tuple[str, list[tuple[str, bool]]]] = {}
    for public_id, slug, status, sla_breached in rows:
        if public_id not in grouped:
            grouped[public_id] = (slug, [])
        grouped[public_id][1].append((status, sla_breached))

    partners: list[TopFailingPartnerData] = []
    for public_id, (slug, terminal_rows) in grouped.items():
        stats = _stats_from_terminal_rows(terminal_rows)
        success_rate = compute_success_rate(stats.delivered, stats.failed)
        if success_rate is None:
            continue
        partners.append(
            TopFailingPartnerData(
                id=public_id,
                slug=slug,
                success_rate=success_rate,
                sla_breaches=stats.sla_breaches,
            )
        )
    partners.sort(key=lambda item: (item.success_rate, -item.sla_breaches))
    return partners[:5]


def _avg_sla_compliance(rows: list[tuple[uuid.UUID, str, str, bool]]) -> float | None:
    grouped: dict[uuid.UUID, list[tuple[str, bool]]] = {}
    for public_id, _slug, status, sla_breached in rows:
        grouped.setdefault(public_id, []).append((status, sla_breached))

    values: list[float] = []
    for terminal_rows in grouped.values():
        stats = _stats_from_terminal_rows(terminal_rows)
        total = stats.delivered + stats.failed
        pct = compute_sla_compliance_pct(stats.sla_not_breached, total)
        if pct is not None:
            values.append(pct)
    if not values:
        return None
    return sum(values) / len(values)


async def build_analytics_overview(
    session: AsyncSession,
    *,
    now: datetime,
) -> OverviewData:
    window_start = _window_start(now)
    dlq_count = await fetch_dlq_backlog_count(session)
    partner_rows = await fetch_overview_partner_rows(session, window_start=window_start)
    return OverviewData(
        window_hours=WINDOW_HOURS,
        dlq_count=dlq_count,
        avg_sla_compliance_pct=_avg_sla_compliance(partner_rows),
        top_failing_partners=_aggregate_overview_partners(partner_rows),
    )


@dataclass(frozen=True, slots=True)
class ComplianceExportRowData:
    partner_slug: str
    success_rate: float | None
    sla_compliance_pct: float | None
    sla_breaches: int
    dlq_count: int


async def fetch_compliance_delivery_rows(
    session: AsyncSession,
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[str, str, bool]]:
    stmt = (
        select(Partner.slug, Delivery.status, Delivery.sla_breached)
        .join(Delivery, Delivery.partner_id == Partner.id)
        .where(
            Delivery.created_at >= window_start,
            Delivery.created_at <= window_end,
            Delivery.status.in_(_TERMINAL_STATUSES),
        )
    )
    result = await session.execute(stmt)
    return cast(list[tuple[str, str, bool]], result.all())


async def fetch_partner_dlq_counts_in_window(
    session: AsyncSession,
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, int]:
    stmt = (
        select(Partner.slug, func.count())
        .join(DeadLetter, DeadLetter.partner_id == Partner.id)
        .where(
            DeadLetter.created_at >= window_start,
            DeadLetter.created_at <= window_end,
            DeadLetter.reason != DeadLetterReason.MANUAL_PURGE.value,
        )
        .group_by(Partner.slug)
    )
    result = await session.execute(stmt)
    return {slug: int(count) for slug, count in result.all()}


def _aggregate_compliance_rows(
    delivery_rows: list[tuple[str, str, bool]],
    dlq_by_slug: dict[str, int],
) -> list[ComplianceExportRowData]:
    grouped: dict[str, list[tuple[str, bool]]] = {}
    for slug, status, sla_breached in delivery_rows:
        grouped.setdefault(slug, []).append((status, sla_breached))

    all_slugs = sorted(set(grouped) | set(dlq_by_slug))
    rows: list[ComplianceExportRowData] = []
    for slug in all_slugs:
        terminal_rows = grouped.get(slug, [])
        stats = _stats_from_terminal_rows(terminal_rows)
        total_terminal = stats.delivered + stats.failed
        rows.append(
            ComplianceExportRowData(
                partner_slug=slug,
                success_rate=compute_success_rate(stats.delivered, stats.failed),
                sla_compliance_pct=compute_sla_compliance_pct(
                    stats.sla_not_breached,
                    total_terminal,
                ),
                sla_breaches=stats.sla_breaches,
                dlq_count=dlq_by_slug.get(slug, 0),
            )
        )
    return rows


async def build_compliance_export(
    session: AsyncSession,
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[ComplianceExportRowData]:
    delivery_rows = await fetch_compliance_delivery_rows(
        session,
        window_start=window_start,
        window_end=window_end,
    )
    dlq_by_slug = await fetch_partner_dlq_counts_in_window(
        session,
        window_start=window_start,
        window_end=window_end,
    )
    return _aggregate_compliance_rows(delivery_rows, dlq_by_slug)


def render_compliance_csv(rows: list[ComplianceExportRowData]) -> str:
    lines = ["partner_slug,success_rate,sla_compliance_pct,sla_breaches,dlq_count"]
    for row in rows:
        success = "" if row.success_rate is None else str(row.success_rate)
        sla_pct = "" if row.sla_compliance_pct is None else str(row.sla_compliance_pct)
        lines.append(f"{row.partner_slug},{success},{sla_pct},{row.sla_breaches},{row.dlq_count}")
    return "\n".join(lines)
