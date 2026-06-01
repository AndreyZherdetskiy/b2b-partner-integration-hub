# SLI / SLO — Partner Integration Hub

Operator interpretation of spec §8.5. Metric **attributes** use `partner_slug` (not UUID `partner_id`) to keep Prometheus cardinality bounded. Spec §8.5.2 table said `partner_id`; this file is the SoT for attribute names.

## SLI → SLO (stand / staging)

| SLI | SLO (Stage 2 target) | Notes |
|-----|----------------------|--------|
| Outbound success (including retries) | ≥ 99.5% / 24h | Stage 1 stand: ≥ 99% with fault injection |
| SLA compliance | ≥ 98% first success within `sla_seconds` | Clock stops at `first_success_at` |
| Terminal failure without DLQ | 0 | Every `failed` has `dead_letters` and/or alert path |
| Inbound duplicate suppression | 100% | Same `Idempotency-Key` → 200, one Kafka message |

## Instruments (OTel → Prometheus)

Names follow spec §8.5.2 (`hub_deliveries_total`, `hub_delivery_attempts_total`, `hub_delivery_duration_seconds`, `hub_dlq_messages_total`, `hub_dlq_backlog`, `hub_dlq_oldest_age_seconds`, `hub_replay_total`, `hub_circuit_breaker_state`, `hub_inbound_events_total`, `hub_inbound_duplicate_suppressed_total`, `hub_rate_limit_rejected_total`, `hub_sla_breaches_total`, `hub_sla_compliance_ratio`, `hub_outbox_unpublished`, `hub_kafka_consumer_lag`) plus `hub_invalid_transition_total`. Stage 1–only `hub_outbox_discrepancy_total` is **retired** from the live catalog (historical ADRs may still mention it).

**Forbidden attributes:** `delivery_id`, `correlation_id`, `trace_id`, UUIDv7 public ids.

## Dashboard interpretation

- Idle `rate(...[5m])` → **NaN** is not an outage.
- A demo 400 (poison) dropping success-rate is **taxonomy**, not infra. Alert `for:` must not fire on a single demo 400.
- Poison vs outage: 4xx non-retryable vs 5xx/timeout/circuit open — see [`runbooks/dlq-response.md`](runbooks/dlq-response.md).
- Rising `hub_outbox_unpublished` means unpublished `outbox_events` rows — check `hub-outbox-relay` and Kafka before blaming partners. See [`runbooks/outbox-lag.md`](runbooks/outbox-lag.md).
- Signing secret rotation overlap is operational, not an SLO breach — see [`runbooks/secret-rotation.md`](runbooks/secret-rotation.md).

## Alerts (minimum)

See `infra/prometheus/alerts.yml`. Each alert has `runbook_url` pointing at `docs/runbooks/...`.

| Alert | Condition (summary) | Runbook |
|-------|---------------------|---------|
| `HubDLQGrowth` | DLQ message rate elevated 5m | [`dlq-response.md`](runbooks/dlq-response.md) |
| `HubDLQAge` | `hub_dlq_oldest_age_seconds` > 3600 for 5m | [`dlq-response.md`](runbooks/dlq-response.md) |
| `HubComplianceDrop` | `hub_sla_compliance_ratio` < 98% for 1h | [`sla-breach-response.md`](runbooks/sla-breach-response.md) |
| `HubCircuitOpen` | circuit open (2) for 5m | [`circuit-breaker.md`](runbooks/circuit-breaker.md) |

`HubComplianceDrop` uses `for: 1h` so a single demo 400 (poison) does not page on-call.
