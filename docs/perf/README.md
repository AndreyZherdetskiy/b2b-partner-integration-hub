# Performance evidence

Recorded load-test results for operator review. These numbers describe **POST → expected HTTP status** on the ingest API (for example 202 on outbound accept), not end-to-end partner webhook delivery SLA and not a contractual throughput guarantee.

| Scenario | Document |
|----------|----------|
| Internal outbound ingest (`POST /internal/v1/outbound/events`) — k6 persist-path regression | [outbound-ingest.md](./outbound-ingest.md) |
| Full-stack Locust accept-path smoke (`GET /inbound/v1/health`, `POST /internal/v1/outbound/events` → 202) | [locust-smoke.md](./locust-smoke.md) |
| Prod-like overlay ceiling hunt (Locust wait=0; Clock A / Clock B; named limiter) | [ceiling-prodlike.md](./ceiling-prodlike.md) |
| Prod-like overlay remesure after Wave 5 (same overlay, rebuilt images, CPU 4.0) | [ceiling-remeasure.md](./ceiling-remeasure.md) |
| Prod-like overlay remesure after persist CTE (one-statement deliveries+outbox INSERT) | [ceiling-persist-cte.md](./ceiling-persist-cte.md) |
| Isolated Kafka `hub.outbound.pending` drain after Locust stop (lag=0 before each hold) | [ceiling-kafka-lag-drain.md](./ceiling-kafka-lag-drain.md) |
| Locust `--otel` + k6 Prometheus remote-write → Grafana (Wave 2 wiring smoke) | [locust-otel-grafana.md](./locust-otel-grafana.md) |

Run k6 via `make load-k6`; Locust headless smoke via `make load-locust`; opt-in Grafana paths via `make load-locust-otel` and `make load-k6-grafana` (not part of `make ci`). See [load-testing runbook](../runbooks/load-testing.md).
