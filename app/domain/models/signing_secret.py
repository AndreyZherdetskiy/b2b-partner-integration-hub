"""Partner signing secret history ORM model (spec §6.3)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryMixin
from app.domain.enums import SigningSecretStatus


class PartnerSigningSecret(Base, UuidPrimaryMixin):
    """Encrypted HMAC signing secret with rotation status and overlap window."""

    __tablename__ = "partner_signing_secrets"

    partner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("partners.id"),
        nullable=False,
    )
    secret_encrypted: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    @property
    def status_enum(self) -> SigningSecretStatus:
        return SigningSecretStatus(self.status)
