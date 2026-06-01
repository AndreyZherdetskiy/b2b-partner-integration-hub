# Architecture — Partner Integration Hub

C4 summary from `spec.md` §4. This file is operator English; it does not replace the spec.

## Context

SaaS domain services and external B2B partners exchange events through the hub. The hub authenticates, signs, retries, dead-letters, and measures first-success SLA. It does not run partner business logic or compute money penalties.

## Containers

| Container | Role |
|-----------|------|
| `hub-migrate` | One-shot `alembic upgrade head` before API/relay/workers |
| `hub-outbound-worker` | Consume pending/retry → HTTP POST to partner |
| `hub-inbound-processor` | May share the API image; routes accepted inbound to Kafka |
| `hub-outbox-relay` | Unpublished `outbox_events` → Kafka (`hub-outbox-relay` Compose service) |
| `hub-scheduler` | Celery beat: maintenance replay/cleanup (not webhook transport) |
| `hub-celery-worker` | Celery worker for scheduled maintenance tasks |
| `hub-celery-beat` | Celery beat scheduler |
| `hub-admin-ui` | Thin SPA on Admin API only |
| PostgreSQL 16.15 | Source of truth |
| Redis 8 | Idempotency cache, rate limit, circuit (S2), Celery broker |
| Kafka 4.3 KRaft | Bus, retry tiers, DLQ |
| OTel Collector | OTLP in; metrics to Prometheus; traces to Jaeger |
| Prometheus / Grafana / Jaeger | Store and visualize |
| `partner-mock` | Local chaos profiles (`ok`, `fail_503`, `fail_400`, `timeout`) |
| `kafbat-ui` | Local Kafka topic/consumer browser (`:8081`) |
| `redis-commander` | Local Redis key browser (`:8082`) |
| `adminer` | Local PostgreSQL browser (`:8083`; server hostname `postgres`) |
| `flower` | Local Celery task/worker monitor (`:8084`; broker Redis DB 1) |

## Local Compose naming

Project name and default network: `b2b-partner-integration-hub`. Named volume: `b2b-partner-integration-hub-postgres-data`. Postgres and Grafana credentials come from gitignored `.env` (tracked template: `.env.example`). Compose stays **one** Kafka broker, RF=1. Compose service `hub-migrate` runs `alembic upgrade head` before `hub-api`, `hub-outbox-relay`, workers, and Celery start. Host-side `make migrate` remains for running the API on the host.

**Infra images:** `kafka-init`, `otel-collector`, `prometheus`, and `grafana` bake configs from `infra/` into their Docker images at build time (no host bind mounts for those config files). `admin_ui/.dockerignore` excludes `node_modules` from the Admin UI build context.

## Kafka

The hub uses **aiokafka**. Every published record uses message key = partner `public_id` (UUIDv7 string). Delivery semantics are **at-least-once** end-to-end; partners deduplicate on `Idempotency-Key`. Celery is **not** the outbox relay and **not** webhook transport.

### Local Compose (single broker)

Docker Compose runs **one** Kafka broker with `KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1` and topic replication factor **RF=1**. This is intentional for local development — do not add extra brokers to Compose to simulate production.

Consumers run as dedicated consumer groups per worker role (`hub-outbound-worker`, inbound processor, outbox relay is a producer-only process).

Stage 1+ topics (minimum): `hub.outbound.pending`, `hub.outbound.retry.30s`, `hub.outbound.retry.1m`, `hub.outbound.retry.5m`, `hub.outbound.retry.15m`, `hub.outbound.retry.1h`, `hub.outbound.dlq`, `hub.inbound.order.created`, `hub.inbound.order.updated`, `hub.integration.sla_breached`.

### Production expectation

Production runs **three** Kafka brokers with topic replication factor **RF=3** and standard consumer groups for each worker fleet. Broker loss should not block committed outbox publishes once the cluster quorum is healthy. Compose RF=1 is **not** a stand-in for production durability.

### Transactional outbox

HTTP handlers and workers enqueue rows in `outbox_events` inside the same database transaction as domain writes. The separate `hub-outbox-relay` process polls `published_at IS NULL`, publishes to Kafka, then marks rows published. Lag is surfaced as gauge `hub_outbox_unpublished` (count of unpublished rows). Metric attributes must not include UUID `delivery_id` or `trace_id`. See [ADR-007](adr/007-transactional-outbox.md) and [`runbooks/outbox-lag.md`](runbooks/outbox-lag.md).

Stage 1 legacy path used publish-after-commit plus `hub_outbox_discrepancy_total` (Stage 1 only; **retired** from the live metric catalog). Stage 2+ uses the transactional outbox only on the persist path.

## PostgreSQL

PostgreSQL 16 is the sole source of truth for deliveries, partners, audit, and outbox state. **No sharding.**

### Optional read replica (production)

An optional PostgreSQL **read replica** may serve Admin **list** queries only: delivery lists, dead-letter lists, and compliance summary/export reads. All writes (`deliveries`, `outbox_events`, `audit_logs`, partner configuration) stay on the **primary**. Replica lag affects list freshness only; it does not change delivery semantics.

## Identifiers

Dual-id only on `partners` and `deliveries` ([ADR-009](adr/009-uuidv7-dual-id.md)). Wire format is UUIDv7 `public_id`.

## Outbox

Transactional outbox ([ADR-007](adr/007-transactional-outbox.md)): enqueue in-request transaction; relay in `hub-outbox-relay`.
