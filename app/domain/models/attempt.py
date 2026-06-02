"""Delivery attempt ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryMixin


class DeliveryAttempt(Base, UuidPrimaryMixin):
    """Single HTTP attempt for a delivery."""

    __tablename__ = "delivery_attempts"
    __table_args__ = (UniqueConstraint("delivery_id", "attempt_number"),)

    delivery_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("deliveries.id"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_headers: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    response_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
