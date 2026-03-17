"""Dead letter ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryMixin


class DeadLetter(Base, UuidPrimaryMixin):
    """DLQ record for a terminal failed delivery."""

    __tablename__ = "dead_letters"

    delivery_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("deliveries.id"),
        unique=True,
        nullable=False,
    )
    partner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("partners.id"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    last_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kafka_offset: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
