"""Process-local TTL cache for outbound accept-path lookups."""

from __future__ import annotations

import time
from uuid import UUID

from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.models.payload_schema import PayloadSchema

DEFAULT_TTL_SECONDS = 30.0

_SCHEMA_ABSENT: object = object()

_cache: dict[str, tuple[float, object]] = {}


def cache_get(key: str) -> object | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        del _cache[key]
        return None
    return value


def cache_set(key: str, value: object, *, ttl: float = DEFAULT_TTL_SECONDS) -> None:
    _cache[key] = (time.monotonic() + ttl, value)


def invalidate_partner(public_id: UUID) -> None:
    partner_key = f"partner:{public_id}"
    entry = _cache.get(partner_key)
    partner_id: int | None = None
    if entry is not None:
        _, value = entry
        if isinstance(value, Partner):
            partner_id = value.id
    _cache.pop(partner_key, None)
    if partner_id is not None:
        _invalidate_endpoints_for_partner(partner_id)


def invalidate_partner_endpoints(partner_id: int) -> None:
    _invalidate_endpoints_for_partner(partner_id)


def invalidate_schema(event_type: str) -> None:
    _cache.pop(f"schema:{event_type}", None)


def reset_accept_path_cache() -> None:
    _cache.clear()


def partner_cache_key(public_id: UUID) -> str:
    return f"partner:{public_id}"


def endpoints_cache_key(partner_id: int, event_type: str) -> str:
    return f"endpoints:{partner_id}:{event_type}"


def schema_cache_key(event_type: str) -> str:
    return f"schema:{event_type}"


def copy_partner(partner: Partner) -> Partner:
    return Partner(
        id=partner.id,
        public_id=partner.public_id,
        slug=partner.slug,
        name=partner.name,
        status=partner.status,
        sla_seconds=partner.sla_seconds,
        rate_limit_rps=partner.rate_limit_rps,
        signing_secret_encrypted=None,
        auto_replay_enabled=partner.auto_replay_enabled,
        circuit_breaker_config=dict(partner.circuit_breaker_config or {}),
    )


def copy_endpoint(endpoint: PartnerEndpoint) -> PartnerEndpoint:
    return PartnerEndpoint(
        id=endpoint.id,
        partner_id=endpoint.partner_id,
        direction=endpoint.direction,
        url=endpoint.url,
        event_types=list(endpoint.event_types or []),
        status=endpoint.status,
        sla_seconds=endpoint.sla_seconds,
        max_attempts=endpoint.max_attempts,
        backoff_policy=dict(endpoint.backoff_policy or {}),
        retry_on_status_codes=list(endpoint.retry_on_status_codes or []),
        timeout_connect_ms=endpoint.timeout_connect_ms,
        timeout_read_ms=endpoint.timeout_read_ms,
    )


def copy_schema(schema_row: PayloadSchema) -> PayloadSchema:
    return PayloadSchema(
        id=schema_row.id,
        event_type=schema_row.event_type,
        version=schema_row.version,
        json_schema=dict(schema_row.json_schema),
        status=schema_row.status,
    )


def cache_schema_value(schema_row: PayloadSchema | None) -> object:
    if schema_row is None:
        return _SCHEMA_ABSENT
    return copy_schema(schema_row)


def schema_from_cache(cached: object) -> PayloadSchema | None:
    if cached is _SCHEMA_ABSENT:
        return None
    if isinstance(cached, PayloadSchema):
        return cached
    return None


def _invalidate_endpoints_for_partner(partner_id: int) -> None:
    prefix = f"endpoints:{partner_id}:"
    for key in list(_cache.keys()):
        if key.startswith(prefix):
            del _cache[key]
