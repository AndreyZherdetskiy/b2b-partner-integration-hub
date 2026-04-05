# Prod-like overlay remesure (Wave 6)

Laptop remesure of the **same** non-default overlay as Wave 4 (`docker-compose.perf.yml` + `make perf-up`), after Wave 5 software (compiled JSON Schema validator reuse, process-wide `get_sessionmaker`) and overlay `hub-api` `cpus: "4.0"`. Images were rebuilt (`--build`) so containers contain that code.

Success on Clock A is HTTP **202** (`status: accepted`) on `POST /internal/v1/outbound/events`. Clock B is delivery drain in PostgreSQL and Kafka consumer lag.

These numbers are **not** spec §8.1 NFR (100 / 500 / 2000 req/s). Do not copy them into the §8.1 table. Wave 4 before-picture: [`ceiling-prodlike.md`](./ceiling-prodlike.md).

## Overlay (inspected live after rebuild)

| Item | Observed |
|------|----------|
| Command | `make perf-up` (`--build`) then `make seed` |
| `hub-api` image | `45112fde3feb` created `2026-04-05T17:17:59Z` (pre-rebuild `14d6dda222af`) |
| `hub-api` Cmd | `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 4` |
| `OTEL_SDK_DISABLED` on API | `true` |
| `hub-api` CPU quota | `HostConfig.NanoCpus=4000000000` (4.0) |
| `hub-api` memory cap | 512 MiB (unchanged) |
| `hub-outbound-worker` | 2 replicas |
| `hub-outbox-relay` | 2 replicas |
| Compose project | `b2b-partner-integration-hub` |
| Partner | `acme-erp` (`make seed`) |
| Redis | `REDIS_URL` left set (circuit breaker). Not emptied. |
| Locust OTEL | `LOAD_LOCUST_OTEL` unset |

Default `make stack-up` stays 1 process per service. Overlay is not default. Prefetch / `max_connections` / `--scale` were not changed for this hunt.

Frozen host ports were free; no sibling numbered `_real_projects` stack was torn down.

## Hunt commands

Export credentials in the **shell** (scripts do not `source .env`):

```bash
set -a && source .env && set +a
unset LOAD_LOCUST_OTEL
```

Closed-loop Locust with think-time 0 (required; smoke defaults 0.1/0.5 stay unchanged):

```bash
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=50 LOAD_SPAWN_RATE=25 LOAD_RUN_TIME=60s make load-locust
# RPS ≈ users / mean_RT → still client-shaped; doubled users to compare Wave 4:
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=100 LOAD_SPAWN_RATE=50 LOAD_RUN_TIME=60s make load-locust
```

Stop signal used: **p50 × 2** on outbound accept vs the 50-user hold (fail% stayed 0; no 5xx; aggregated RPS did not rise). Artifacts: `.local/locust/smoke_stats.csv` (gitignored).

## Clock A (accept) — before vs after

CSV columns: `# reqs`, RPS, fail%, p50/p99. Date: 2026-04-05. Wave 4 figures from [`ceiling-prodlike.md`](./ceiling-prodlike.md).

### 50 users, spawn 25/s, 60s, wait=0

| Name | Wave 4 # reqs | Wave 4 RPS | Wave 4 p50 / p99 (ms) | Remesure # reqs | Remesure RPS | Remesure Fail % | Remesure p50 / p99 (ms) |
|------|---------------|------------|------------------------|-----------------|--------------|-----------------|-------------------------|
| `GET /inbound/v1/health` | 1205 | 20.46 | 290 / 700 | 4602 | 77.98 | 0 | 66 / 190 |
| `POST /internal/v1/outbound/events` | 3351 | 56.89 | 690 / 1900 | 13804 | 233.89 | 0 | 170 / 510 |
| **Aggregated** | **4556** | **77.35** | **600 / 1800** | **18406** | **311.87** | **0** | **140 / 480** |

Closed-loop check: 50 / 0.158 s mean ≈ 316 RPS vs observed 311.87 — still client-shaped. Doubled users.

Outbound accept ~4.1× Wave 4 RPS; p50 690 → 170 ms.

### 100 users, spawn 50/s, 60s, wait=0 (stop)

| Name | Wave 4 # reqs | Wave 4 RPS | Wave 4 p50 / p99 (ms) | Remesure # reqs | Remesure RPS | Remesure Fail % | Remesure p50 / p99 (ms) |
|------|---------------|------------|------------------------|-----------------|--------------|-----------------|-------------------------|
| `GET /inbound/v1/health` | 1233 | 20.90 | 600 / 1300 | 4314 | 73.12 | 0 | 160 / 350 |
| `POST /internal/v1/outbound/events` | 3640 | 61.70 | 1400 / 3000 | 12923 | 219.05 | 0 | 380 / 790 |
| **Aggregated** | **4873** | **82.60** | **1200 / 2900** | **17237** | **292.17** | **0** | **340 / 770** |

Outbound p50 170 → 380 ms (**×2.24** vs this remesure's 50-user hold). Aggregated RPS 312 → 292 (users ×2 did not raise throughput). Health is in-process liveness (no DB) and its p50 also rose (66 → 160 ms, **×2.42**).

Outbound accept ~3.5× Wave 4 RPS at 100 users; p50 1400 → 380 ms.

## Clock B (delivery)

SQL via `docker compose -p b2b-partner-integration-hub exec -T postgres psql -U hub -d hub`. Baseline before hunt: unpublished 0, `deliveries` all `delivered` (8532 leftover on the volume), `pg_stat_activity` 8, `max_connections` 100, Kafka `hub.outbound.pending` lag unset (no offsets yet).

### Mid-run

| Probe | 50 users (during 60s hold) | 100 users (SQL during 60s hold; CPU from a concurrent 25s 100-user hold) |
|-------|----------------------------|--------------------------------------------------------------------------|
| `outbox_events` unpublished (`published_at IS NULL`) | 153 | 132 |
| `deliveries` | delivered 10937, pending 9079 | delivered 19702, pending 14916 |
| `pg_stat_activity` count | 44 | 63 |
| `max_connections` | 100 | 100 |
| idle in transaction | 17 | 22 |
| Kafka `hub.outbound.pending` lag | 9880 | 15666 |
| `hub-api` docker stats | 397.90% CPU, 411/512 MiB | 398.41% then 402.24% CPU, 421/512 MiB |
| `postgres` docker stats | 280.89% CPU | 505.96% then 440.47% CPU |
| `kafka` docker stats | 29.69% CPU | 23.44% / 22.67% CPU |
| `hub-outbox-relay` | 10.61% / 15.22% | 1.85% / 45.77% then 31.50% / 10.83% |
| `hub-outbound-worker` | 0.26% / 71.15% | 0.29% / 59.42% then 0.29% / 64.61% |

100-user CPU samples were taken while Locust was still ramped (not after shutdown). The first 60s 100-user `docker stats` probe landed after Locust exit and is discarded.

Kafka topics are **1 partition** (`infra/kafka/create-topics.sh`). Consumer group `hub-outbound-worker` therefore assigns `hub.outbound.pending` to **one** member — replica 1 stays near 0% CPU. That is Clock B topology, not the Clock A stop.

Unlike Wave 4, pending lag **held** through the accept holds (thousands, not tens) because accept RPS now exceeds the single assigned worker.

### After (drain)

Unpublished returned to 0 after both 60s holds. Deliveries and Kafka lag were **still draining** when probed (not fully caught up in the hunt window):

| After | unpublished | deliveries | pending lag | notes |
|-------|-------------|------------|-------------|-------|
| 50-user 60s hold | 0 | delivered 14668, pending 7834 | 7622 | worker-2 ~67% CPU |
| 100-user 60s hold | 0 | delivered 25196, pending 10407 | 10189 | worker-2 ~66% CPU; API idle |

## Named limiter

**API/process** (uvicorn already 4 workers; they now consume Compose `cpus: "4.0"` on `hub-api` and peg that quota).

Plan-table match: API CPU pegged at the **4.0** quota (~398–402%); health (no DB) p50 more than doubled 50u→100u; unpublished did not accumulate as the Clock A backlog (153/132 mid-hold, 0 after); fail% 0; outbound p50 ×2.24 with RPS plateau.

The limiter **class did not move**. Wave 5 software plus the honest 4.0 CPU quota raised Clock A (50u outbound 57 → 234 RPS; p50 690 → 170 ms). The stop is still accept-path process time under the API container quota, not relay/publish and not `max_connections`.

Postgres CPU is a **close second / next** limiter (281% at 50u; 440–506% at 100u) but health has no DB and still slowed, so it is not the Clock A primary.

Not chosen:

| Candidate | Why not primary |
|-----------|-----------------|
| pool budget vs `max_connections` | No HTTP 500 / `TooManyConnections`. Activity 63/100, not at cap. |
| relay/publish | Unpublished 153/132 mid-hold then 0 after. Relays were busy, not idle-while-unpublished-grows. |
| workers | Kafka pending lag **held** (Clock B). One assigned consumer ~60–71% CPU. That did not stop Clock A (HTTP 202); workers are behind the faster accept path. |
| still client (wait 0.1–0.5) | Hunt used wait=0. RPS≈users/mean_RT is expected for closed-loop; stop was p50×2 + RPS plateau. |
| DB | Postgres CPU 281–506% is concurrent write load. Health-path p50 still doubled. Fail% stayed 0; activity not at cap. Likely the **next** limiter if API quota or process cost is reduced further. |

Do **not** treat `--scale`, `max_connections++`, prefetch inflation, or another overlay CPU bump as the fix.

## k6 `ramping-arrival-rate` — skipped

`make load-k6-grafana` is **constant VUs** (`K6_VUS` / `K6_DURATION` in `scripts/load_k6_grafana.sh` + `load/k6/outbound_ingest.js` `options.vus` / `duration`). It does not implement k6 `ramping-arrival-rate`. Scheduled arrival vs achieved RPS was **not** measured.

Ramping hunt skipped. Thresholds stay enabled; do not pass `--no-thresholds`.

## What this did **not** prove

- Spec §8.1 Stage 1/2/3 throughput or p95 delivery overhead
- Inbound HMAC/argon2 path (not in this Locust mix)
- Partner webhook SLA / `first_success_at` under production Kafka RF
- Full Clock B drain at the new accept rate (lag held; 1-partition workers)
- Isolating Postgres vs API if health were kept fast while outbound p50 climbed
- Open-loop arrival (Locust is closed-loop even at wait=0)

## Related

- Wave 4 before-picture: [`ceiling-prodlike.md`](./ceiling-prodlike.md)
- Overlay knobs and how-to-hunt: [`docs/runbooks/load-testing.md`](../runbooks/load-testing.md)
- Accept-path smoke (default stack, wait 0.1–0.5): [`locust-smoke.md`](./locust-smoke.md)
- Plan: [`docs/plans/2026-04-04-remeasure.md`](../plans/2026-04-04-remeasure.md)
