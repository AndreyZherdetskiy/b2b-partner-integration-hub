"""JSON Schema validation against registered payload schemas."""

from __future__ import annotations

from uuid import UUID

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema.validators import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import PayloadSchemaStatus
from app.domain.models.payload_schema import PayloadSchema

_VALIDATORS: dict[tuple[UUID, int], Draft202012Validator] = {}


class SchemaValidationError(Exception):
    """Payload failed validation against a registered JSON Schema."""


def _validator_for(schema_row: PayloadSchema) -> Draft202012Validator:
    key = (schema_row.id, schema_row.version)
    validator = _VALIDATORS.get(key)
    if validator is None:
        validator = Draft202012Validator(schema_row.json_schema)
        _VALIDATORS[key] = validator
    return validator


def validate_payload(
    event_type: str,
    payload: dict[str, object],
    schema_row: PayloadSchema | None,
) -> None:
    """No row or deprecated → accept. Invalid → SchemaValidationError."""
    if schema_row is None or schema_row.status != PayloadSchemaStatus.ACTIVE:
        return
    if schema_row.event_type != event_type:
        return
    try:
        _validator_for(schema_row).validate(payload)
    except JsonSchemaValidationError as exc:
        raise SchemaValidationError(str(exc)) from exc


async def fetch_latest_active_schema(
    session: AsyncSession,
    event_type: str,
) -> PayloadSchema | None:
    """Return the highest-version active schema for event_type, if any."""
    result = await session.execute(
        select(PayloadSchema)
        .where(
            PayloadSchema.event_type == event_type,
            PayloadSchema.status == PayloadSchemaStatus.ACTIVE,
        )
        .order_by(PayloadSchema.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
