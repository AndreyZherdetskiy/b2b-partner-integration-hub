"""Partner ORM model (dual-id)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, DualIdMixin
from app.domain.enums import PartnerStatus


class Partner(Base, DualIdMixin):
    """B2B integration partner."""

    __tablename__ = "partners"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PartnerStatus.PROVISIONING,
    )
    sla_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_replay_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    circuit_breaker_config: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    rate_limit_rps: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    signing_secret_encrypted: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
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
