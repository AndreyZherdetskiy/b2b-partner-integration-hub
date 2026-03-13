# ADR-009: UUIDv7 dual-id for partners and deliveries only

- **Status:** Accepted
- **Date:** 2026-03-13
- **Spec:** §6.3, §12.9

## Context

External contracts need stable opaque IDs for API, Kafka, UI, and replay. Internal FKs benefit from compact BIGINT. Composite PKs on tenant-like tables complicate ORM and mix natural uniqueness with identity.

## Decision

| Entity | PK | Public |
|--------|----|--------|
| `partners`, `deliveries` | BIGINT | `public_id` UUIDv7 UNIQUE |
| `partner_endpoints`, `delivery_attempts`, `dead_letters`, `inbound_events`, `partner_signing_secrets`, `partner_api_keys`, `audit_logs` | UUIDv7 | = PK |
| `outbox_events` | BIGINT | none (never in API) |

Rules:

- Generate `public_id` in application code before insert (UUIDv7 library).
- JSON/OpenAPI/Admin UI/Kafka/replay use **public** UUIDs only. Field name in JSON **may** be `id` when it is the public UUID — never the sequential BIGINT.
- FKs **to** dual-id tables are BIGINT.
- Natural UNIQUE, not composite PK: `partners.slug`; `(partner_id, idempotency_key)` on deliveries and inbound_events; `(delivery_id, attempt_number)` on attempts.
- `audit_logs.resource_id` is the UUIDv7 public id of the resource.
- Correlation ids are UUIDv7. Invalid version/format → **422**.

## Consequences

- API boundary must resolve `public_id` → internal `id`.
- OpenAPI unit tests fail if Partner/Delivery schemas expose sequential integer `id`.

## Alternatives considered

- UUIDv4 everywhere — worse B-tree locality.
- Dual-id on every table — noise for satellites.
- Composite PK `(partner_id, …)` — rejected (spec §6.3).

## Links

- Spec §6.3, §12.9
