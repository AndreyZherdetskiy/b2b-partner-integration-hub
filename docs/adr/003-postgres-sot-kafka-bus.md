# ADR-003: PostgreSQL source of truth, Kafka bus, aiokafka client

- **Status:** Accepted
- **Date:** 2026-06-01
- **Spec:** §4.10, §5, §12.3, §12.11

## Context

Admin API/UI needs queryable delivery status, attempts, filters, and SLA flags. A pure event-sourced store would slow MVP without helping webhook operations. Spec §5 allows `aiokafka` or `confluent-kafka`.

## Decision

1. **PostgreSQL** is the source of truth for partners, deliveries, attempts, DLQ, audit, and (Stage 2) outbox.
2. **Kafka** is the event bus (pending, retry tiers, DLQ, inbound, SLA breached). Message **key** = partner **`public_id`** (UUIDv7) so events for one partner stay ordered on a partition.
3. **Kafka client: `aiokafka`** for API producer and async workers. Stay consistent; do not mix `confluent-kafka` in the same processes without a new ADR amendment.

Stage 1 may publish to Kafka after the delivery row commits, and **must** increment `hub_outbox_discrepancy_total` (or equivalent) when publish fails after commit. Stage 2 **Must** replace that path with `outbox_events` + `hub-outbox-relay` (ADR-007).

## Consequences

- Admin list/get are SQL, not Kafka scans.
- Hot partitions for very large partners — mitigated later by more partitions / endpoint routing (Stage 3).
- Compose Kafka RF=1; production RF=3 is documented in `docs/architecture.md`, not faked with unused brokers.

## Alternatives considered

- Event sourcing as SoT — poor Support/BizDev query story.
- Status only in Redis — not durable.
- `confluent-kafka` — valid; rejected for this repo to keep one async-native client.

## Links

- Spec §12.3, §12.11
- ADR-007
