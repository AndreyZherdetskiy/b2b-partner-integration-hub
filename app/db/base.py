"""SQLAlchemy declarative base and shared mixins."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Identity
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.ids import generate_uuidv7


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class DualIdMixin:
    """BIGINT identity PK + UUIDv7 public_id (ADR-009)."""

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    public_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        unique=True,
        nullable=False,
        default=generate_uuidv7,
    )


class UuidPrimaryMixin:
    """UUIDv7 primary key for satellite entities."""

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=generate_uuidv7,
    )

    @property
    def public_id(self) -> uuid.UUID:
        """ADR-010 wire id alias; PK is already UUIDv7 (not DualIdMixin)."""
        return self.id
