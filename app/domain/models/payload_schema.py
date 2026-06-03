"""Payload JSON Schema registry ORM model (Stage 3 stub)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryMixin
from app.domain.enums import PayloadSchemaStatus


class PayloadSchema(Base, UuidPrimaryMixin):
    """Registered JSON Schema for an event_type version."""

    __tablename__ = "payload_schemas"
    __table_args__ = (
        UniqueConstraint("event_type", "version", name="uq_payload_schemas_event_type_version"),
    )

    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    json_schema: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PayloadSchemaStatus.ACTIVE,
    )
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
