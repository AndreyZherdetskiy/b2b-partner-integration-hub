# ADR-002: Kafka retry topics, not Celery countdown

- **Status:** Accepted
- **Date:** 2026-03-11
- **Spec:** §4.5, §4.6, §12.2

## Context

Outbound webhooks need delayed retries, isolation between delay tiers, lag observability, and a natural DLQ. Celery ETA/countdown is familiar but is the wrong primary transport at 500k–2M deliveries/day.

## Decision

Retries go through Kafka topics `hub.outbound.retry.{tier}`. Stage 1 uses a single tier `hub.outbound.retry.30s`. Stage 2 adds `5m` / `15m` / `1h` (and keeps `30s` for attempt 2). After max attempts or a non-retryable status, publish `hub.outbound.dlq` and insert `dead_letters`, then **commit the consumer offset**.

**Celery** is reserved for scheduled maintenance: stale-failed replay, idempotency-key purge, secret-rotation notify. Celery must not POST webhooks as the delivery transport.

## Consequences

- More topics and consumer groups to operate.
- Per-tier lag metrics and AsyncAPI contracts.
- Poison messages must not block a partition.

## Alternatives considered

- Single Redis ZSET delay queue — weaker journal/retention/AsyncAPI story.
- Sleep in the consumer — blocks the partition.
- Celery as sole transport — poor tier isolation and webhook-scale audit.

## Links

- Spec §12.2
- ADR-001, ADR-003
