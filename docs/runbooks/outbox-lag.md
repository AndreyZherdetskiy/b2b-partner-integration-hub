# Runbook: Outbox lag

**Metric:** `hub_outbox_unpublished`
**Compose service:** `hub-outbox-relay`
**Table:** `outbox_events` (`published_at IS NULL`)

## Symptoms

- `hub_outbox_unpublished` gauge rising or stuck above zero for extended periods
- Deliveries or inbound events accepted (202) but no Kafka consumer progress
- Grafana outbox panel elevated; partner-visible latency without DLQ growth

## Checks

1. Confirm `hub-outbox-relay` is running and healthy (`docker compose -p b2b-partner-integration-hub ps hub-outbox-relay`).
2. Inspect relay logs for `outbox_publish_failed` or Kafka connection errors.
3. Verify Kafka broker health (`kafka` service; Kafbat UI on `:8081` for topic presence).
4. Query unpublished count: `SELECT count(*) FROM outbox_events WHERE published_at IS NULL;`
5. If count is high but relay is publishing, check consumer lag on downstream topics (`hub_kafka_consumer_lag`).
6. Distinguish hub incident (relay/Kafka) from partner outage (DLQ / circuit open) — see [`dlq-response.md`](dlq-response.md).

## Safe actions

- Restart `hub-outbox-relay` after confirming Kafka is reachable (relay is idempotent; rows remain unpublished until `published_at` is set).
- Scale relay replicas in production only when ops policy allows multiple publishers (Compose runs a single relay).
- Escalate to platform if Kafka cluster quorum or RF=3 broker loss is suspected.

## Do not

- Set `published_at` manually on `outbox_events` without a Kafka publish — causes silent message loss.
- Delete unpublished outbox rows.
- Run Celery or ad-hoc scripts as a substitute relay.
- Add high-cardinality attributes (`delivery_id`, `trace_id`) to `hub_outbox_unpublished` when debugging.

## Escalation

If unpublished count grows while relay logs show repeated publish failures, treat as a Kafka or hub incident. If relay is healthy but consumers stall, inspect `hub-outbound-worker` and inbound processor consumer groups.
