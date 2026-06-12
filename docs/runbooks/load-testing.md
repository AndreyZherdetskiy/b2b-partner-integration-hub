# Load testing runbook

Operator guide for **local** HTTP load smoke against the full Docker Compose stack. These runs measure API accept paths (for example HTTP **202** on outbound ingest), not end-to-end partner webhook delivery SLA and not spec §8.1 NFR throughput claims.

## CI and pre-commit (Wave 3)

GitHub Actions (`.github/workflows/ci.yml`) adds sibling jobs `load-harness` (`make load-harness` — load group pytest + Locust `--list`, **no** stack) and `load-locust-smoke` (`cp .env.example .env`, `make stack-up`, `make load-locust`, always `make stack-down`; default smoke **without** `LOAD_LOCUST_OTEL=1`).

Pre-commit (`.pre-commit-config.yaml`) adds Ruff **0.5.7**, standard file hooks, and a **local** `import loadtests.locustfile` check — **no Docker** in hooks. Install: `uv run pre-commit install` ([`CONTRIBUTING.md`](../../CONTRIBUTING.md)). On a fresh/untracked repo, `pre-commit run --all-files` only sees `git ls-files`; use `pre-commit run --files <paths>` for local proof.

Plan: [`docs/plans/2026-06-08-ci-pre-commit.md`](../plans/2026-06-08-ci-pre-commit.md).

## Prerequisites

1. Frozen host ports are free (see `AGENTS.md` §1.1). If another Compose project on this machine holds them, stop **that project only** with `docker compose -p <project> down` — no `-v`, no prune.
2. Copy env template: `cp .env.example .env` (gitignored; never commit).
3. Install Python deps including the load group: `uv sync --group load`.
4. Bring up the **full** stack (API, workers, relay, Kafka, Postgres, Redis, observability):

   ```bash
   make stack-up    # alias for compose-up --build --wait (1 process per service)
   make seed        # not automatic after stack-up
   ```

   For prod-like ceiling hunts (Wave 4), use the **non-default** overlay instead:

   ```bash
   make perf-up     # docker-compose.perf.yml + scaled consumers (not spec §8.1 proof)
   make seed
   ```

### Prod-like overlay knobs (`docker-compose.perf.yml`)

| Knob | Default `stack-up` | `make perf-up` overlay |
|------|--------------------|------------------------|
| `hub-api` uvicorn workers | 1 (`--factory`) | 4 (`--workers 4`) |
| `hub-api` CPU limit (`deploy.resources.limits.cpus`) | `1.0` | `4.0` (characterization — not spec §8.1 proof) |
| `hub-outbound-worker` replicas | 1 | 2 (`--scale`) |
| `hub-outbox-relay` replicas | 1 | 2 (`--scale`) |
| `OTEL_SDK_DISABLED` on API | `false` (from `.env`) | `true` (overlay) |
| Locust think-time (`LOAD_WAIT_MIN`/`MAX`) | smoke 0.1/0.5 | hunt uses `0`/`0` |
| Kafka `max.poll.records` / prefetch | code default | **unchanged** |

`make stack-down` always includes `-f docker-compose.perf.yml` so scaled replicas do not stay up. Default `make stack-up` does **not** apply the overlay.

5. Confirm core services are running:

   ```bash
   docker compose -p b2b-partner-integration-hub ps
   ```

   Expect `hub-api` healthy, plus `hub-outbound-worker` and `hub-outbox-relay` up. Do **not** load-test a host-only uvicorn process — traffic must hit `http://127.0.0.1:8000` (Compose `hub-api`).

### How to hunt (Clock A / Clock B)

Use this only on the overlay. Do not treat laptop RPS as spec §8.1. Do not `--scale` or raise `max_connections` as the “fix”. Last recorded hunt: [`docs/perf/ceiling-prodlike.md`](../perf/ceiling-prodlike.md) (Wave 4). Remesure after Wave 5 software + `cpus: "4.0"`: [`docs/perf/ceiling-remeasure.md`](../perf/ceiling-remeasure.md). Remesure after Wave 7 software (pure ASGI + accept-path L1 cache): [`docs/perf/ceiling-accept-path.md`](../perf/ceiling-accept-path.md). Remesure after Wave 8 software (insert-first idempotency + `pool_pre_ping=False`): [`docs/perf/ceiling-db-roundtrip.md`](../perf/ceiling-db-roundtrip.md). Remesure after persist CTE (one-statement deliveries+outbox INSERT): [`docs/perf/ceiling-persist-cte.md`](../perf/ceiling-persist-cte.md). Isolated Kafka pending drain (lag=0 before each hold): [`docs/perf/ceiling-kafka-lag-drain.md`](../perf/ceiling-kafka-lag-drain.md).

1. Frozen ports free. `make perf-up` then `make seed`. Inspect: `hub-api` Cmd has `--factory` and `--workers 4`; two `hub-outbound-worker`; two `hub-outbox-relay`; `OTEL_SDK_DISABLED=true` on the API.
2. `set -a && source .env && set +a`. Unset `LOAD_LOCUST_OTEL`. Do not empty `REDIS_URL`.
3. Clock A: `LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=50 LOAD_SPAWN_RATE=25 LOAD_RUN_TIME=60s make load-locust`. Read `.local/locust/smoke_stats.csv` (`# reqs`, RPS, fail%, p50/p99). If RPS ≈ users / mean response time, still client-shaped — double users and rerun until a stop signal (fail% > 1, p50 × 2, CPU peg, 5xx).
4. Clock B mid-run and after:

   ```bash
   docker compose -p b2b-partner-integration-hub exec -T postgres psql -U hub -d hub -c \
     "SELECT count(*) FILTER (WHERE published_at IS NULL) AS unpublished FROM outbox_events;"
   docker compose -p b2b-partner-integration-hub exec -T postgres psql -U hub -d hub -c \
     "SELECT status, count(*) FROM deliveries GROUP BY status;"
   docker compose -p b2b-partner-integration-hub exec -T postgres psql -U hub -d hub -c \
     "SELECT count(*) AS activity FROM pg_stat_activity; SHOW max_connections;"
   docker stats --no-stream
   ```

   Kafka group lag only if cheap (`kafka-consumer-groups.sh` in the `kafka` container, or kafbat-ui `:8081`).
5. Name **one** primary limiter from the observation table in [`docs/plans/2026-06-13-ceiling-hunt.md`](../plans/2026-06-13-ceiling-hunt.md). `make load-k6-grafana` is constant VUs — skip `ramping-arrival-rate` unless you add a separate script. No `--no-thresholds`.
6. `make stack-down` (overlay included). Confirm empty `docker compose -p b2b-partner-integration-hub ps`. No `-v`.

## Credentials (fail-closed)

Load scripts **do not** `source .env`. Export variables in your shell before running Make targets:

```bash
set -a && source .env && set +a
```

Preflight requires `ADMIN_BOOTSTRAP_TOKEN` or `LOAD_ADMIN_TOKEN` in the **process environment**. Without them the script exits before Locust starts:

```bash
env -u ADMIN_BOOTSTRAP_TOKEN -u LOAD_ADMIN_TOKEN ./scripts/load_smoke.sh
# stderr: preflight failed: ADMIN_BOOTSTRAP_TOKEN or LOAD_ADMIN_TOKEN must be set ...
# exit code: 1; zero Locust HTTP requests
```

## Locust smoke (accept path)

Headless smoke — preflight, then Locust with HTML/CSV under `.local/locust/` (gitignored):

```bash
make load-locust
```

Defaults (override via env): `LOAD_HOST=http://127.0.0.1:8000`, `LOAD_USERS=2`, `LOAD_SPAWN_RATE=1`, `LOAD_RUN_TIME=10s`.

Tasks exercised:

| Weight | Endpoint | Success |
|--------|----------|---------|
| health | `GET /inbound/v1/health` | HTTP 200 |
| outbound | `POST /internal/v1/outbound/events` | HTTP **202** accepted |

Recorded facts from the last live run: [`docs/perf/locust-smoke.md`](../perf/locust-smoke.md).

### Locust web UI (optional)

```bash
make load-locust-ui
```

Binds Locust web UI on host port **8089**. The script exits nonzero if `:8089` is already in use (fail-closed). Stop with Ctrl+C when finished.

## k6 persist-path regression (Stage 3)

k6 remains the scripted regression for outbound ingest persist latency. It is **not** replaced by Locust.

```bash
export K6_PARTNER_PUBLIC_ID=<acme-erp public_id>
set -a && source .env && set +a
make load-k6
```

Details: [`docs/perf/outbound-ingest.md`](../perf/outbound-ingest.md).

## Locust OTEL → Grafana (opt-in, Wave 2)

Requires full stack up (Collector OTLP HTTP on host **4318**, Prometheus **9090** with remote-write receiver). Scripts fail-closed when the Compose network `b2b-partner-integration-hub` or `:4318` is missing.

```bash
set -a && source .env && set +a
make load-locust-otel
```

Exports OTLP metrics/traces to the existing Collector; Prometheus scrapes `otel-collector:8889`. Grafana dashboard **Locust OTEL (Collector)** (`hub-locust-otel`) is baked from [`docs/grafana/dashboards/locust-otel.json`](../grafana/dashboards/locust-otel.json). Panels use metric names observed in Prometheus after a live run — see [`docs/perf/locust-otel-grafana.md`](../perf/locust-otel-grafana.md).

Fail-closed before stack-up (expect nonzero):

```bash
LOAD_LOCUST_OTEL=1 ./scripts/load_smoke.sh
# stderr: preflight failed (no token) or health check refused (stack down)
```

Default `make load-locust` does **not** pass `--otel`.

## k6 Grafana remote-write (opt-in, Wave 2)

k6 on the Compose network with Prometheus remote-write (distinct from host-network `make load-k6`):

```bash
set -a && source .env && set +a
make load-k6-grafana
```

Uses `BASE=http://hub-api:8000`, `K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write`, and stdin script delivery (WSL-safe). Thresholds remain enabled (`http_req_failed` rate). Grafana dashboard **19665** family: [`docs/grafana/dashboards/k6-prometheus.json`](../grafana/dashboards/k6-prometheus.json). Live metric names: [`docs/perf/locust-otel-grafana.md`](../perf/locust-otel-grafana.md).

## Artifacts and cleanup

| Path | Contents |
|------|----------|
| `.local/locust/smoke.html` | Locust HTML report (headless smoke) |
| `.local/locust/smoke_*.csv` | Locust CSV stats |

Do not commit `.local/`. Tear down when finished:

```bash
make stack-down   # compose down --remove-orphans; no -v
```

## What these tests do **not** prove

- Partner webhook round-trip or `delivered` state
- Kafka consumer lag or outbox relay throughput at production scale
- Contractual SLA or validated **2M deliveries/day** capacity (spec §8.1)
- Production-scale Grafana SLO interpretation (local dashboards prove wiring only)

For SLA interpretation use [`docs/slo.md`](../slo.md) and production metrics — not local smoke RPS.
