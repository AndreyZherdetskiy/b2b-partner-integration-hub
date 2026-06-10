# Locust OTEL + k6 Grafana remote-write (Wave 2)

## What this measures

Opt-in Locust `--otel` metrics exported via the existing OTel Collector (`:4318` HTTP) into Prometheus, plus k6 `experimental-prometheus-rw` on the Compose network. These are **local operator smokes** for observability wiring — not spec §8.1 NFR proof.

## Prerequisites

Same as [`docs/runbooks/load-testing.md`](../runbooks/load-testing.md): `make stack-up` (rebuilds Prometheus RW receiver + Grafana dashboards), `make seed`, export env:

```bash
set -a && source .env && set +a
```

## Fail-closed (stack down)

Before `make stack-up` (2026-06-10):

```bash
LOAD_LOCUST_OTEL=1 ./scripts/load_smoke.sh
```

Observed stderr:

```text
preflight failed: ADMIN_BOOTSTRAP_TOKEN or LOAD_ADMIN_TOKEN must be set in process environment
```

Exit code: **1**.

With dummy token while stack is down:

```text
preflight failed: health check failed: [Errno 111] Connection refused
```

Exit code: **1**. OTEL network/:4318 checks run only after preflight passes.

## Locust OTEL smoke

Command:

```bash
set -a && source .env && set +a
make load-locust-otel
```

| Parameter | Value |
|-----------|-------|
| `LOAD_USERS` | 2 |
| `LOAD_SPAWN_RATE` | 1 |
| `LOAD_RUN_TIME` | 10s |
| OTLP endpoint | `http://127.0.0.1:4318` (`http/protobuf`) |
| `OTEL_SERVICE_NAME` | `locust` |

Observed Locust stdout: **57** requests, **0** failures (~5.8 req/s aggregate). OpenTelemetry log exporter returned HTTP **404** (Collector has metrics/traces pipelines only; logs not configured).

### Prometheus metric names (Locust / OTEL client, this run)

Queried: `curl -s http://127.0.0.1:9090/api/v1/label/__name__/values`

| Metric | Notes |
|--------|-------|
| `locust_users_count` | Labels: `user_class`, `exported_job="locust"` |
| `locust_client_duration_seconds_bucket` | Histogram; label `name` = endpoint path |
| `locust_client_duration_seconds_count` | Counter companion |
| `locust_client_duration_seconds_sum` | Counter companion |
| `http_client_duration_milliseconds_bucket` | OTEL HTTP client histogram |
| `http_client_duration_milliseconds_count` | Counter companion |
| `http_client_duration_milliseconds_sum` | Counter companion |

All scraped via `job="otel-collector"` (`exported_job="locust"`). Dashboard: [`docs/grafana/dashboards/locust-otel.json`](../grafana/dashboards/locust-otel.json) (uid `hub-locust-otel`, datasource uid `Prometheus`).

## k6 Grafana remote-write smoke

Command:

```bash
set -a && source .env && set +a
make load-k6-grafana
```

| Parameter | Value |
|-----------|-------|
| Image | `grafana/k6:0.54.0` |
| Network | `b2b-partner-integration-hub` |
| `BASE` | `http://hub-api:8000` |
| RW URL | `http://prometheus:9090/api/v1/write` |
| VUs / duration | 2 / 15s (script defaults) |

Observed: **1236** iterations, **0** `http_req_failed`, checks **100%** pass (`status is 202 accepted`).

### Prometheus metric names (k6, this run)

| Metric |
|--------|
| `k6_checks_rate` |
| `k6_data_received_total` |
| `k6_data_sent_total` |
| `k6_http_req_blocked_p99` |
| `k6_http_req_connecting_p99` |
| `k6_http_req_duration_p99` |
| `k6_http_req_failed_rate` |
| `k6_http_req_receiving_p99` |
| `k6_http_req_sending_p99` |
| `k6_http_req_tls_handshaking_p99` |
| `k6_http_req_waiting_p99` |
| `k6_http_reqs_total` |
| `k6_iteration_duration_p99` |
| `k6_iterations_total` |
| `k6_vus` |
| `k6_vus_max` |

Dashboard: official Grafana.com **19665** family at [`docs/grafana/dashboards/k6-prometheus.json`](../grafana/dashboards/k6-prometheus.json).

## What these tests do **not** prove

- Contractual SLA or validated **2M deliveries/day** capacity (spec §8.1)
- Partner webhook round-trip or delivery state
- Production-scale Kafka / outbox relay throughput
