"""Unit tests for SQLAlchemy mapped columns, uniques, and indexes (spec §6.3–6.4)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import BigInteger, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.domain.ids import generate_uuidv7
from app.domain.models.api_key import PartnerApiKey
from app.domain.models.attempt import DeliveryAttempt
from app.domain.models.audit import AuditLog
from app.domain.models.dead_letter import DeadLetter
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.inbound_event import InboundEvent
from app.domain.models.outbox import OutboxEvent
from app.domain.models.partner import Partner


def _unique_column_sets(table) -> list[tuple[str, ...]]:
    sets: list[tuple[str, ...]] = []
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            sets.append(tuple(constraint.columns.keys()))
    return sets


def _index_column_sets(table) -> list[tuple[str, ...]]:
    sets: list[tuple[str, ...]] = []
    for index in table.indexes:
        sets.append(tuple(index.columns.keys()))
    return sets


def _gin_indexes(table) -> Iterable[Index]:
    return [
        idx
        for idx in table.indexes
        if idx.dialect_options.get("postgresql", {}).get("using") == "gin"
    ]


def test_generate_uuidv7_is_version_7() -> None:
    value = generate_uuidv7()
    assert isinstance(value, uuid.UUID)
    assert value.version == 7


def test_partner_dual_id_columns() -> None:
    table = Partner.__table__
    assert isinstance(table.c.id.type, BigInteger)
    assert table.c.id.primary_key
    assert isinstance(table.c.public_id.type, PG_UUID)
    assert table.c.public_id.unique
    assert isinstance(table.c.signing_secret_encrypted.type, BYTEA)


def test_partner_slug_unique_not_pk() -> None:
    table = Partner.__table__
    assert ("slug",) in _unique_column_sets(table)
    assert not any(
        isinstance(c, UniqueConstraint)
        and c.columns.keys() == ["slug"]
        and c.columns["slug"].primary_key
        for c in table.constraints
    )


def test_delivery_dual_id_and_partner_idempotency_unique() -> None:
    table = Delivery.__table__
    assert isinstance(table.c.id.type, BigInteger)
    assert table.c.id.primary_key
    assert isinstance(table.c.public_id.type, PG_UUID)
    assert table.c.public_id.unique
    assert isinstance(table.c.partner_id.type, BigInteger)
    assert ("partner_id", "idempotency_key") in _unique_column_sets(table)


def test_delivery_indexes() -> None:
    table = Delivery.__table__
    index_sets = _index_column_sets(table)
    assert ("status", "next_retry_at") in index_sets
    assert ("partner_id", "created_at") in index_sets
    assert ("correlation_id",) in index_sets
    assert ("partner_id", "sla_breached", "created_at") in index_sets


def test_partner_endpoint_gin_and_partner_status_index() -> None:
    table = PartnerEndpoint.__table__
    assert ("partner_id", "status") in _index_column_sets(table)
    gin_cols = {tuple(idx.columns.keys()) for idx in _gin_indexes(table)}
    assert ("event_types",) in gin_cols


def test_delivery_attempt_unique_attempt_number() -> None:
    table = DeliveryAttempt.__table__
    assert isinstance(table.c.delivery_id.type, BigInteger)
    assert ("delivery_id", "attempt_number") in _unique_column_sets(table)


def test_dead_letter_delivery_id_unique() -> None:
    table = DeadLetter.__table__
    assert ("delivery_id",) in _unique_column_sets(table)


def test_inbound_event_partner_idempotency_unique() -> None:
    table = InboundEvent.__table__
    assert ("partner_id", "idempotency_key") in _unique_column_sets(table)


def test_audit_log_resource_id_is_uuid() -> None:
    table = AuditLog.__table__
    assert isinstance(table.c.resource_id.type, PG_UUID)


def test_outbox_no_public_id_bigint_pk() -> None:
    table = OutboxEvent.__table__
    assert isinstance(table.c.id.type, BigInteger)
    assert table.c.id.primary_key
    assert "public_id" not in table.c
    index_sets = _index_column_sets(table)
    assert ("published_at", "created_at") in index_sets


def test_uuid_pk_tables_use_uuid_not_bigint() -> None:
    for model in (
        PartnerEndpoint,
        DeliveryAttempt,
        DeadLetter,
        InboundEvent,
        PartnerApiKey,
        AuditLog,
    ):
        table = model.__table__
        pk_col = next(col for col in table.c if col.primary_key)
        assert isinstance(pk_col.type, PG_UUID), model.__name__


def test_fk_to_dual_id_tables_are_bigint() -> None:
    for table_name, col_name in (
        (PartnerEndpoint.__table__, "partner_id"),
        (Delivery.__table__, "partner_id"),
        (DeliveryAttempt.__table__, "delivery_id"),
        (DeadLetter.__table__, "delivery_id"),
        (DeadLetter.__table__, "partner_id"),
        (InboundEvent.__table__, "partner_id"),
        (PartnerApiKey.__table__, "partner_id"),
        (OutboxEvent.__table__, "aggregate_id"),
    ):
        assert isinstance(table_name.c[col_name].type, BigInteger), f"{table_name.name}.{col_name}"


def test_delivery_endpoint_fk_is_uuid() -> None:
    assert isinstance(Delivery.__table__.c.endpoint_id.type, PG_UUID)
