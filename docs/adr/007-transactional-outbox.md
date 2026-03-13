# ADR-007: Transactional outbox for outbound deliveries

- **Status:** Accepted
- **Date:** 2026-03-12
- **Spec:** §4.10, §12.7

## Context

Inserting `deliveries` and independently producing to Kafka (dual-write) loses events when publish fails after commit — a silent miss with no DLQ row.

## Decision

**Stage 2 Must:** in the same PostgreSQL transaction as creating the delivery (or inbound accept / admin replay), insert `outbox_events`. Process `hub-outbox-relay` publishes then sets `published_at`. Index `(published_at NULLS FIRST, created_at)`. Outbox PK is BIGINT append-only and **never** appears in the HTTP API.

**Stage 2 persist path:** API inbound/outbound/replay handlers enqueue outbox only — no `producer.send` in the request path. `message_key` stores partner `public_id` for Kafka partitioning.

**Worker retry/DLQ/SLA:** `outbound_processor` may still produce to Kafka directly after the delivery attempt row commits (avoids relay lag on retries). That path is not dual-write with the persist transaction.

**Stage 1 allowed:** publish-after-commit **only** if publish failures increment `hub_outbox_discrepancy_total` (or equivalent) and operators can replay. This is an acknowledged gap, not a silent dual-write. Removed from persist handlers in Stage 2 Task 2; catch-up is `hub-outbox-relay` (no discrepancy path on persist).

## Consequences

- Extra process and lag metric `hub_outbox_unpublished`.
- Prove Stage 2: Kafka down after insert → unpublished row → relay catch-up.

## Alternatives considered

- Kafka as SoT — fights Admin query patterns (ADR-003).
- CDC — heavier than needed for this hub.
- Leaving Stage 1 path after Stage 2 — forbidden.

## Links

- Spec §12.7
- ADR-003
