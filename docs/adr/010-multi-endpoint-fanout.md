# ADR-010: Multi-endpoint event_type fan-out

- **Status:** Accepted
- **Date:** 2026-03-13
- **Spec:** §3.5, §7.1.5

## Context

Partners may register multiple active outbound webhook URLs subscribed to the same `event_type`. Stage 2 selected only the first matching endpoint (`.limit(1)`), so additional URLs never received events.

Deliveries retain `UNIQUE (partner_id, idempotency_key)` (ADR-009). A single caller idempotency key must fan out to N endpoints without violating that constraint.

## Decision

1. **Fan-out:** `fetch_active_outbound_endpoints` returns all active outbound endpoints whose `event_types` contain the requested type. One request creates N `deliveries` rows and N `outbox_events` rows in a single transaction (ADR-007). No Kafka produce in the HTTP handler.

2. **Derived idempotency keys:** Stored `idempotency_key = f"{client_key}::{endpoint_public_id}"`. Caller key is persisted as `source_event_id`.

3. **Strict duplicate:** If any delivery for the partner already has `source_event_id == client_key`, return **200** with all matching `delivery_ids` and insert nothing — even when new endpoints were added since the first request.

4. **HTTP response:** `{ "delivery_id": "<first>", "delivery_ids": ["..."], "status": "accepted"|"duplicate" }`. `delivery_id` remains for backward compatibility.

## Consequences

- Replays and operator tools must use delivery `public_id`, not caller idempotency key alone, when targeting a single URL.
- Endpoint add/remove after first accept does not retroactively deliver under the same caller key.

## Alternatives considered

- `UNIQUE (partner_id, idempotency_key, endpoint_id)` — rejected; changes Stage 1/2 constraint and migration surface.
- Per-endpoint duplicate 200 only when all endpoints already have rows — rejected; strict `source_event_id` is simpler for callers.

## Links

- [ADR-007 transactional outbox](./007-transactional-outbox.md)
- [ADR-009 dual-id](./009-uuidv7-dual-id.md)
- [SQLAlchemy 2.0 — ScalarResult.scalars()](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Result.scalars)
