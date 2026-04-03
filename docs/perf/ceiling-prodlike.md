# Prod-like overlay ceiling hunt (Clock A / Clock B)

Laptop characterization of the **non-default** Compose overlay (`docker-compose.perf.yml` + `make perf-up`). Success on Clock A is HTTP **202** (`status: accepted`) on `POST /internal/v1/outbound/events`. Clock B is delivery drain in PostgreSQL and (when cheap) Kafka lag.

These numbers are **not** spec §8.1 NFR (100 / 500 / 2000 req/s). Do not copy them into the §8.1 table.

## Overlay (inspected live)

| Item | Observed |
|------|----------|
| Command | `make perf-up` then `make seed` |
| `hub-api` Cmd | `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 4` |
| `OTEL_SDK_DISABLED` on API | `true` |
| `hub-outbound-worker` | 2 replicas |
| `hub-outbox-relay` | 2 replicas |
| Compose project | `b2b-partner-integration-hub` |
| Partner | `acme-erp` (`make seed`) |
| Redis | `REDIS_URL` left set (circuit breaker). Not emptied. |
| Locust OTEL | `LOAD_LOCUST_OTEL` unset |

Default `make stack-up` stays 1 process per service. Overlay is not default.

`hub-api` still has Compose `deploy.resources.limits.cpus: "1.0"` and `memory: 512M` from `docker-compose.yml` (overlay does not raise them). Four uvicorn workers therefore share **one** CPU quota.

## Hunt commands

Export credentials in the **shell** (scripts do not `source .env`):

```bash
set -a && source .env && set +a
unset LOAD_LOCUST_OTEL
```

Closed-loop Locust with think-time 0 (required; smoke defaults 0.1/0.5 stay unchanged):

```bash
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=50 LOAD_SPAWN_RATE=25 LOAD_RUN_TIME=60s make load-locust
# RPS ≈ users / mean_RT → still client-shaped; doubled users:
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=100 LOAD_SPAWN_RATE=50 LOAD_RUN_TIME=60s make load-locust
```

Stop signal used: **p50 × 2** on outbound accept (fail% stayed 0; no 5xx). Artifacts: `.local/locust/smoke_stats.csv` (gitignored).

## Clock A (accept)

CSV columns: `# reqs`, RPS, fail%, p50/p99. Date: 2026-04-03.

### 50 users, spawn 25/s, 60s, wait=0

| Name | # reqs | RPS | Fail % | p50 (ms) | p99 (ms) |
|------|--------|-----|--------|----------|----------|
| `GET /inbound/v1/health` | 1205 | 20.46 | 0 | 290 | 700 |
| `POST /internal/v1/outbound/events` | 3351 | 56.89 | 0 | 690 | 1900 |
| **Aggregated** | **4556** | **77.35** | **0** | **600** | **1800** |

Closed-loop check: 50 / 0.637 s mean ≈ 78.5 RPS vs observed 77.35 — still client-shaped. Doubled users.

### 100 users, spawn 50/s, 60s, wait=0 (stop)

| Name | # reqs | RPS | Fail % | p50 (ms) | p99 (ms) |
|------|--------|-----|--------|----------|----------|
| `GET /inbound/v1/health` | 1233 | 20.90 | 0 | 600 | 1300 |
| `POST /internal/v1/outbound/events` | 3640 | 61.70 | 0 | 1400 | 3000 |
| **Aggregated** | **4873** | **82.60** | **0** | **1200** | **2900** |

Outbound p50 690 → 1400 ms (**×2.03**). Aggregated RPS 77 → 83 (users ×2 did not raise throughput). Health is in-process liveness (`HealthResponse(status="ok")`, no DB) and its p50 also doubled (290 → 600 ms).

## Clock B (delivery)

SQL via `docker compose -p b2b-partner-integration-hub exec -T postgres psql -U hub -d hub`. Baseline before hunt: unpublished 0, `deliveries` all `delivered` (1313 leftover from prior smoke on the volume), `pg_stat_activity` 8, `max_connections` 100.

### Mid-run

| Probe | 50 users | 100 users |
|-------|----------|-----------|
| `outbox_events` unpublished (`published_at IS NULL`) | 0 | 34 |
| `deliveries` | delivered 4292, pending 99 | delivered 7109, pending 127 |
| `pg_stat_activity` count | 40 | 61 |
| `max_connections` | 100 | 100 |
| idle in transaction | 20 | 32 |
| Kafka `hub.outbound.pending` lag | 0 (sampled near end of hold) | 88 |
| `hub-api` docker stats | 102% CPU, 409/512 MiB | 104% CPU, 425/512 MiB |
| `postgres` docker stats | 41% CPU | 70% CPU |
| `kafka` docker stats | 21% CPU | 179% CPU |
| `hub-outbox-relay` | ~0.3% / 5.2% | ~5.0% / 0.4% |
| `hub-outbound-worker` | 62% / 0.4% | 51% / 0.2% |

Kafka topics are **1 partition** (`infra/kafka/create-topics.sh`). Consumer group `hub-outbound-worker` therefore assigns `hub.outbound.pending` to **one** member — replica 2 stays near 0% CPU. That is Clock B topology, not the Clock A stop.

### After (drain)

Both holds drained: unpublished 0, all sampled deliveries `delivered` (4750 after 50u; 8532 after 100u), Kafka pending lag 0, activity back to 29 idle/low.

## Named limiter

**API/process** (uvicorn already 4 workers; they share Compose `cpus: "1.0"` on `hub-api`).

Plan-table match: API CPU pegged at the container quota (~102–104%); health (no DB) p50 doubled; unpublished did not accumulate as the primary backlog; fail% 0.

Not chosen:

| Candidate | Why not primary |
|-----------|-----------------|
| pool budget vs `max_connections` | No HTTP 500 / `TooManyConnections`. Activity 61/100, not at cap. |
| relay/publish | Unpublished 0 at 50u; 34 mid-100u then 0 after. Relay CPU low. Kafka pending not empty while relays idle. |
| workers | Lag 88 mid-100u, 0 after. Did not hold. Worker CPU is Clock B; 1-partition assignment explains replica 2 idle. |
| still client (wait 0.1–0.5) | Hunt used wait=0. RPS≈users/mean_RT is expected for closed-loop; stop was p50×2 + RPS plateau. |
| DB | Postgres CPU 70% at 100u is concurrent write load, not the health-path signal. Fail% stayed 0; activity not at cap. May become the **next** limiter if API CPU quota is raised. |

Do **not** treat `--scale`, `max_connections++`, or prefetch inflation as the fix.

## Intended Wave 5 change (not remesured)

Wave 5 software fixes on the accept path (not re-run on this laptop yet):

- Reuse compiled `Draft202012Validator` instances keyed by `(PayloadSchema.id, version)` instead of constructing on every outbound enqueue.
- Cache one SQLAlchemy async engine/sessionmaker per `database_url` in workers/relay (`get_sessionmaker`); API lifespan already holds a single engine.

No new RPS claims here; remeasure is Wave 6 after overlay CPU honesty (Task 2).

## k6 `ramping-arrival-rate` — skipped

`make load-k6-grafana` is **constant VUs** (`K6_VUS` / `K6_DURATION` in `scripts/load_k6_grafana.sh` + `load/k6/outbound_ingest.js` `options.vus` / `duration`). It does not implement k6 `ramping-arrival-rate`. Scheduled arrival vs achieved RPS was **not** measured.

Ramping hunt skipped. Optional constant-VU Grafana smoke was not required to name the limiter (Locust already stopped on p50×2). Thresholds stay enabled; do not pass `--no-thresholds`.

## What this did **not** prove

- Spec §8.1 Stage 1/2/3 throughput or p95 delivery overhead
- Inbound HMAC/argon2 path (not in this Locust mix)
- Partner webhook SLA / `first_success_at` under production Kafka RF
- Raising Compose `cpus` / memory, pool size, or Kafka partitions (read-only hunt)
- Open-loop arrival (Locust is closed-loop even at wait=0)

## Related

- Overlay knobs and how-to-hunt: [`docs/runbooks/load-testing.md`](../runbooks/load-testing.md)
- Accept-path smoke (default stack, wait 0.1–0.5): [`locust-smoke.md`](./locust-smoke.md)
- Plan: [`docs/plans/2026-04-03-ceiling-hunt.md`](../plans/2026-04-03-ceiling-hunt.md)
