"""Shared partner seed definitions and idempotent upsert helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.domain.enums import EndpointDirection, EndpointStatus, PartnerStatus
from app.domain.models.api_key import PartnerApiKey
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.services.api_keys import generate_api_key, generate_signing_secret
from app.domain.services.secrets import encrypt_signing_secret
from app.domain.services.signing_secrets import insert_primary_signing_secret

PARTNER_MOCK_BASE = "http://partner-mock:8090"
CANONICAL_EVENT_TYPES = ("order.created", "order.updated")
INBOUND_WRITE_SCOPE = "inbound:write"

CANONICAL_SLUGS: tuple[str, ...] = (
    "acme-erp",
    "flaky-logistics",
    "strict-payments",
    "slow-crm",
)


@dataclass(frozen=True, slots=True)
class PartnerSeed:
    slug: str
    name: str
    webhook_path: str
    sla_seconds: int
    status: PartnerStatus = PartnerStatus.ACTIVE
    timeout_read_ms: int = 10_000


def canonical_partner_seeds() -> tuple[PartnerSeed, ...]:
    return (
        PartnerSeed(
            slug="acme-erp",
            name="Acme ERP",
            webhook_path="/webhook",
            sla_seconds=60,
        ),
        PartnerSeed(
            slug="flaky-logistics",
            name="Flaky Logistics",
            webhook_path="/webhook/fail_503_then_ok",
            sla_seconds=60,
        ),
        PartnerSeed(
            slug="strict-payments",
            name="Strict Payments",
            webhook_path="/webhook/fail_400",
            sla_seconds=60,
        ),
        PartnerSeed(
            slug="slow-crm",
            name="Slow CRM",
            webhook_path="/webhook/timeout",
            sla_seconds=30,
            timeout_read_ms=5_000,
        ),
    )


def prod_like_extra_seeds() -> tuple[PartnerSeed, ...]:
    return (
        PartnerSeed(
            slug="zenith-erp",
            name="Zenith ERP",
            webhook_path="/webhook",
            sla_seconds=90,
        ),
        PartnerSeed(
            slug="continental-logistics",
            name="Continental Logistics",
            webhook_path="/webhook/fail_503_then_ok",
            sla_seconds=120,
        ),
        PartnerSeed(
            slug="apex-payments",
            name="Apex Payments",
            webhook_path="/webhook",
            sla_seconds=45,
            status=PartnerStatus.SUSPENDED,
        ),
        PartnerSeed(
            slug="velocity-crm",
            name="Velocity CRM",
            webhook_path="/webhook",
            sla_seconds=15,
        ),
        PartnerSeed(
            slug="northstar-erp",
            name="Northstar ERP",
            webhook_path="/webhook/fail_429",
            sla_seconds=75,
        ),
        PartnerSeed(
            slug="harbor-logistics",
            name="Harbor Logistics",
            webhook_path="/webhook",
            sla_seconds=180,
        ),
    )


def resolve_database_url(raw_url: str) -> str:
    """Use localhost when scripts run on the host against Compose Postgres."""
    if "@postgres:" in raw_url:
        return raw_url.replace("@postgres:", "@localhost:")
    return raw_url


def build_settings() -> Settings:
    settings = get_settings()
    database_url = resolve_database_url(settings.database_url)
    return settings.model_copy(update={"database_url": database_url})


def _webhook_url(path: str) -> str:
    return f"{PARTNER_MOCK_BASE}{path}"


async def _load_partner(session: AsyncSession, slug: str) -> Partner | None:
    result = await session.execute(select(Partner).where(Partner.slug == slug))
    return result.scalar_one_or_none()


async def _load_outbound_endpoint(
    session: AsyncSession,
    partner_id: int,
) -> PartnerEndpoint | None:
    result = await session.execute(
        select(PartnerEndpoint).where(
            PartnerEndpoint.partner_id == partner_id,
            PartnerEndpoint.direction == EndpointDirection.OUTBOUND,
        )
    )
    return result.scalar_one_or_none()


async def _load_active_api_key(session: AsyncSession, partner_id: int) -> PartnerApiKey | None:
    result = await session.execute(
        select(PartnerApiKey).where(
            PartnerApiKey.partner_id == partner_id,
            PartnerApiKey.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def upsert_partner_seed(
    session: AsyncSession,
    seed: PartnerSeed,
    *,
    fernet_key: str,
    print_secrets: bool,
) -> dict[str, Any]:
    partner = await _load_partner(session, seed.slug)
    created_partner = partner is None
    signing_secret: str | None = None
    api_key_plain: str | None = None

    if partner is None:
        signing_secret = generate_signing_secret()
        encrypted = encrypt_signing_secret(signing_secret.encode("utf-8"), fernet_key)
        partner = Partner(
            slug=seed.slug,
            name=seed.name,
            status=seed.status,
            sla_seconds=seed.sla_seconds,
            rate_limit_rps=100,
            signing_secret_encrypted=encrypted,
        )
        session.add(partner)
        await session.flush()
        insert_primary_signing_secret(
            session,
            partner_id=partner.id,
            secret_encrypted=encrypted,
            version=1,
        )
    else:
        partner.name = seed.name
        partner.status = seed.status
        partner.sla_seconds = seed.sla_seconds

    endpoint = await _load_outbound_endpoint(session, partner.id)
    if endpoint is None:
        endpoint = PartnerEndpoint(
            partner_id=partner.id,
            direction=EndpointDirection.OUTBOUND,
            url=_webhook_url(seed.webhook_path),
            event_types=list(CANONICAL_EVENT_TYPES),
            status=EndpointStatus.ACTIVE,
            sla_seconds=seed.sla_seconds,
            timeout_read_ms=seed.timeout_read_ms,
        )
        session.add(endpoint)
    else:
        endpoint.url = _webhook_url(seed.webhook_path)
        endpoint.event_types = list(CANONICAL_EVENT_TYPES)
        endpoint.status = EndpointStatus.ACTIVE
        endpoint.sla_seconds = seed.sla_seconds
        endpoint.timeout_read_ms = seed.timeout_read_ms

    api_key = await _load_active_api_key(session, partner.id)
    if api_key is None:
        api_key_plain, prefix, key_hash = generate_api_key()
        session.add(
            PartnerApiKey(
                partner_id=partner.id,
                key_prefix=prefix,
                key_hash=key_hash,
                scopes=[INBOUND_WRITE_SCOPE],
            )
        )

    await session.flush()

    result: dict[str, Any] = {
        "slug": seed.slug,
        "partner_public_id": str(partner.public_id),
        "created": created_partner,
    }
    if print_secrets and signing_secret is not None:
        result["signing_secret"] = signing_secret
    if print_secrets and api_key_plain is not None:
        result["api_key"] = api_key_plain
    return result


async def _seed_partners_async(
    seeds: tuple[PartnerSeed, ...],
    *,
    print_secrets: bool = False,
) -> list[dict[str, Any]]:
    settings = build_settings()
    if not settings.fernet_key:
        msg = "FERNET_KEY is required for seeding signing secrets."
        raise SystemExit(msg)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    results: list[dict[str, Any]] = []
    try:
        async with sessionmaker() as session:
            for seed in seeds:
                row = await upsert_partner_seed(
                    session,
                    seed,
                    fernet_key=settings.fernet_key,
                    print_secrets=print_secrets,
                )
                results.append(row)
            await session.commit()
    finally:
        await engine.dispose()
    return results


def seed_partners(
    seeds: tuple[PartnerSeed, ...],
    *,
    print_secrets: bool = False,
) -> list[dict[str, Any]]:
    return asyncio.run(_seed_partners_async(seeds, print_secrets=print_secrets))
