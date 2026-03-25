"""Signing secret rotation and lookup (spec §6.3, ADR-004)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.enums import SigningSecretStatus
from app.domain.models.audit import AuditLog
from app.domain.models.partner import Partner
from app.domain.models.signing_secret import PartnerSigningSecret
from app.domain.services.api_keys import generate_signing_secret
from app.domain.services.secrets import decrypt_signing_secret, encrypt_signing_secret


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _decrypt_row(row: PartnerSigningSecret, fernet_key: str) -> bytes:
    return decrypt_signing_secret(row.secret_encrypted, fernet_key)


def _previous_is_valid(row: PartnerSigningSecret, now: datetime) -> bool:
    if row.status != SigningSecretStatus.PREVIOUS.value:
        return False
    if row.valid_until is None:
        return True
    return row.valid_until > now


def insert_primary_signing_secret(
    session: AsyncSession,
    *,
    partner_id: int,
    secret_encrypted: bytes,
    version: int = 1,
    now: datetime | None = None,
) -> PartnerSigningSecret:
    current_time = now or _utcnow()
    row = PartnerSigningSecret(
        partner_id=partner_id,
        secret_encrypted=secret_encrypted,
        version=version,
        status=SigningSecretStatus.PRIMARY.value,
        valid_from=current_time,
        valid_until=None,
    )
    session.add(row)
    return row


async def _fetch_primary_row(
    session: AsyncSession,
    partner_id: int,
) -> PartnerSigningSecret | None:
    result = await session.execute(
        select(PartnerSigningSecret).where(
            PartnerSigningSecret.partner_id == partner_id,
            PartnerSigningSecret.status == SigningSecretStatus.PRIMARY.value,
        )
    )
    return result.scalar_one_or_none()


async def _fetch_previous_rows(
    session: AsyncSession,
    partner_id: int,
) -> list[PartnerSigningSecret]:
    result = await session.execute(
        select(PartnerSigningSecret).where(
            PartnerSigningSecret.partner_id == partner_id,
            PartnerSigningSecret.status == SigningSecretStatus.PREVIOUS.value,
        )
    )
    return list(result.scalars().all())


async def load_inbound_signing_secrets(
    session: AsyncSession,
    partner: Partner,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[bytes | None, bytes | None]:
    current_time = now or _utcnow()
    primary_row = await _fetch_primary_row(session, partner.id)
    if primary_row is None:
        if not partner.signing_secret_encrypted:
            return None, None
        try:
            secret = decrypt_signing_secret(partner.signing_secret_encrypted, settings.fernet_key)
        except Exception:
            return None, None
        return secret, None

    try:
        primary = _decrypt_row(primary_row, settings.fernet_key)
    except Exception:
        return None, None

    previous: bytes | None = None
    for row in await _fetch_previous_rows(session, partner.id):
        if _previous_is_valid(row, current_time):
            try:
                previous = _decrypt_row(row, settings.fernet_key)
            except Exception:
                continue
            break
    return primary, previous


async def load_outbound_primary_secret(
    session: AsyncSession,
    partner: Partner,
    settings: Settings,
) -> bytes | None:
    primary_row = await _fetch_primary_row(session, partner.id)
    if primary_row is not None:
        try:
            return _decrypt_row(primary_row, settings.fernet_key)
        except Exception:
            return None
    if not partner.signing_secret_encrypted:
        return None
    try:
        return decrypt_signing_secret(partner.signing_secret_encrypted, settings.fernet_key)
    except Exception:
        return None


async def rotate_partner_signing_secret(
    session: AsyncSession,
    partner: Partner,
    settings: Settings,
    actor_id: str,
    *,
    now: datetime | None = None,
) -> str:
    current_time = now or _utcnow()
    overlap = timedelta(hours=settings.hub_secret_rotation_overlap_hours)
    plaintext = generate_signing_secret()
    encrypted = encrypt_signing_secret(plaintext.encode("utf-8"), settings.fernet_key)

    current_primary = await _fetch_primary_row(session, partner.id)
    next_version = 1
    if current_primary is not None:
        next_version = current_primary.version + 1
        current_primary.status = SigningSecretStatus.PREVIOUS.value
        current_primary.valid_until = current_time + overlap
        for row in await _fetch_previous_rows(session, partner.id):
            if row.id != current_primary.id:
                row.status = SigningSecretStatus.REVOKED.value

    insert_primary_signing_secret(
        session,
        partner_id=partner.id,
        secret_encrypted=encrypted,
        version=next_version,
        now=current_time,
    )
    partner.signing_secret_encrypted = encrypted

    session.add(
        AuditLog(
            actor_id=actor_id,
            action="signing_secret.rotate",
            resource_type="partner",
            resource_id=partner.public_id,
            metadata_={"version": next_version},
        )
    )
    await session.commit()
    return plaintext
