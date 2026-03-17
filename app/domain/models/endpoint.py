"""Partner endpoint ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryMixin
from app.domain.enums import EndpointStatus


class PartnerEndpoint(Base, UuidPrimaryMixin):
    """Inbound or outbound webhook endpoint for a partner."""

    __tablename__ = "partner_endpoints"
    __table_args__ = (
        Index("ix_partner_endpoints_partner_id_status", "partner_id", "status"),
        Index("ix_partner_endpoints_event_types", "event_types", postgresql_using="gin"),
    )

    partner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("partners.id"),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String()), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=EndpointStatus.ACTIVE,
    )
    sla_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=8)
    backoff_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    retry_on_status_codes: Mapped[list[int]] = mapped_column(
        ARRAY(Integer),
        nullable=False,
        server_default="{}",
    )
    timeout_connect_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=3000)
    timeout_read_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
