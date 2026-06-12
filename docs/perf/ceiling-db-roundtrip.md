# Prod-like overlay remesure (Wave 8)

Laptop remesure of the **same** non-default overlay as Wave 6/7 (`docker-compose.perf.yml` + `make perf-up`), after Wave 8 software (insert-first idempotency on outbound accept — SELECT deliveries only after `IntegrityError`; API engine `pool_pre_ping=False`). Images were rebuilt (`--build`) so containers contain that code.

Success on Clock A is HTTP **202** (`status: accepted`) on `POST /internal/v1/outbound/events`. Clock B is delivery drain in PostgreSQL and Kafka consumer lag.

These numbers are **not** spec §8.1 NFR (100 / 500 / 2000 req/s). Do not copy them into the §8.1 table. Wave 7 before-picture: [`ceiling-accept-path.md`](./ceiling-accept-path.md).

## Overlay (inspected live after rebuild)

| Item | Observed |
|------|----------|
| Command | `make perf-up` (`--build`) then `make seed` |
| `hub-api` image | `5562fff622ea` created `2026-06-12T18:40:08Z` (Wave 7 remesure image was `bf53722e2b72`) |
| `hub-api` Cmd | `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 4` |
| `OTEL_SDK_DISABLED` on API | `true` |
| `hub-api` CPU quota | `HostConfig.NanoCpus=4000000000` (4.0) |
| `hub-api` memory cap | 512 MiB (unchanged; ~410 MiB during 100u) |
| `hub-outbound-worker` | 2 replicas |
| `hub-outbox-relay` | 2 replicas |
| Compose project | `b2b-partner-integration-hub` |
| Partner | `acme-erp` (`make seed`) |
| Redis | `REDIS_URL` left set (circuit breaker). Not emptied. |
| Locust OTEL | `LOAD_LOCUST_OTEL` unset |

Default `make stack-up` stays 1 process per service. Overlay is not default. Prefetch / `max_connections` / `--scale` / overlay CPU were not changed for this hunt.

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
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=100 LOAD_SPAWN_RATE=50 LOAD_RUN_TIME=60s make load-locust
```

Stop signal used: **p50 × 2** on outbound accept vs the 50-user hold, health p50 also doubled, API cgroup pegged at 4.0 (fail% stayed 0; no 5xx; outbound RPS did not scale with users). Artifacts: `.local/locust/smoke_stats.csv` (gitignored).

No extra diagnostic 100-user hold: `docker stats --no-stream` returned plausible `hub-api` percentages on both prescribed holds.

## Clock A (accept) — Wave 7 vs Wave 8

CSV columns: `# reqs`, RPS, fail%, p50/p99. Date: 2026-06-12. Wave 7 figures from [`ceiling-accept-path.md`](./ceiling-accept-path.md).

### 50 users, spawn 25/s, 60s, wait=0

| Name | Wave 7 # reqs | Wave 7 RPS | Wave 7 p50 / p99 (ms) | Wave 8 # reqs | Wave 8 RPS | Wave 8 Fail % | Wave 8 p50 / p99 (ms) |
|------|---------------|------------|------------------------|---------------|------------|---------------|-------------------------|
| `GET /inbound/v1/health` | 4554 | 77.03 | 14 / 93 | 8122 | 137.28 | 0 | 14 / 110 |
| `POST /internal/v1/outbound/events` | 13962 | 236.17 | 180 / 490 | 23811 | 402.46 | 0 | 85 / 440 |
| **Aggregated** | **18516** | **313.21** | **150 / 470** | **31933** | **539.74** | **0** | **46 / 600** |

Closed-loop check: 50 / 0.089 s mean ≈ 561 RPS vs observed 539.74 — still client-shaped. Doubled users.

Outbound accept ~1.70× Wave 7 RPS (402 vs 236); p50 180 → 85 ms. Health p50 stayed 14 ms (no DB).

### 100 users, spawn 50/s, 60s, wait=0 (stop)

| Name | Wave 7 # reqs | Wave 7 RPS | Wave 7 p50 / p99 (ms) | Wave 8 # reqs | Wave 8 RPS | Wave 8 Fail % | Wave 8 p50 / p99 (ms) |
|------|---------------|------------|------------------------|---------------|------------|---------------|-------------------------|
| `GET /inbound/v1/health` | 4949 | 83.60 | 20 / 110 | 8330 | 140.57 | 0 | 32 / 160 |
| `POST /internal/v1/outbound/events` | 14836 | 250.60 | 350 / 890 | 24789 | 418.31 | 0 | 180 / 580 |
| **Aggregated** | **19785** | **334.20** | **290 / 860** | **33119** | **558.88** | **0** | **150 / 790** |

Outbound p50 85 → 180 ms (**×2.12** vs this remesure's 50-user hold). Outbound RPS 402 → 418 (users ×2 did not raise throughput). Health p50 14 → 32 ms (**×2.29**).

Outbound accept ~1.67× Wave 7 RPS at 100 users; p50 350 → 180 ms.

## Clock B (delivery)

SQL via `docker compose -p b2b-partner-integration-hub exec -T postgres psql -U hub -d hub`. Baseline before hunt: unpublished 0, `deliveries` all `delivered` (91428 leftover on the volume), `pg_stat_activity` 8, idle-in-transaction 0, `max_connections` 100, Kafka `hub.outbound.pending` lag unset (no offsets yet).

CPU uses `docker stats --no-stream` (usable this hunt) plus `cpu.stat` (`usage_usec` delta) on the API and postgres cgroups.

### Mid-run

| Probe | 50 users (during 60s hold) | 100 users (during 60s hold) |
|-------|----------------------------|-----------------------------|
| `outbox_events` unpublished (`published_at IS NULL`) | 3 | 259 |
| `deliveries` | delivered 93465, pending 14699 | delivered 100298, pending 30271 |
| `pg_stat_activity` count | 36 | 58 |
| `max_connections` | 100 | 100 |
| idle in transaction | 15 | 24 |
| Kafka `hub.outbound.pending` lag | 17028 | 31559 |
| `hub-api` CPU | docker **401.52%**; cgroup **2.80 cores / 4.0** over 10.0 s (`nr_throttled` 122→135) | docker **402.69%**; cgroup **3.94 cores / 4.0** over 10.0 s (`nr_throttled` 305→339) |
| `postgres` docker stats / cgroup | docker 111.95%; cgroup **0.79** cores / 10.0 s | docker 105.10%; cgroup **1.29** cores / 10.0 s |
| `kafka` docker stats | 13.70% | 32.29% |
| `hub-outbox-relay` | 3.06% / 3.71% | 47.63% / 18.19% |
| `hub-outbound-worker` | 0.54% / 69.56% | 0.33% / 73.27% |

`nr_throttled` after the 50u hold: 135. After the 100u hold: 360.

Kafka topics are **1 partition** (`infra/kafka/create-topics.sh`). Consumer group `hub-outbound-worker` therefore assigns `hub.outbound.pending` to **one** member — replica 1 stays near 0% CPU. That is Clock B topology, not the Clock A stop.

Pending lag **held** through the accept holds (thousands) because accept RPS still exceeds the single assigned worker.

### After (drain)

Unpublished returned to 0 after both 60s holds. Deliveries and Kafka lag were **still draining** when probed (not fully caught up in the hunt window):

| After | unpublished | deliveries | pending lag | notes |
|-------|-------------|------------|-------------|-------|
| 50-user 60s hold | 0 | delivered 96678, pending 18930 | 18648 | worker-2 draining |
| 100-user 60s hold | 0 | delivered 106008, pending 34678 | 34431 | worker-2 still draining (~42% CPU); API idle |

## Named limiter

**API/process** (uvicorn still 4 workers; they now peg Compose `cpus: "4.0"` on `hub-api` again).

Plan-table match: outbound p50 ×2.12 with RPS plateau; fail% 0; health (no DB) p50 14→32 ms (**×2.29**). 100u API cgroup **3.94 / 4.0** with `nr_throttled` rising in the sample window. Unpublished did not accumulate as the Clock A backlog (3/259 mid-hold, **0 after**).

The limiter **class moved**. Wave 8 software raised 50u outbound RPS (236 → 402) and cut 50u outbound p50 (180 → 85). At 100 users the accept path is fast enough that Clock A hits the existing 4.0 API quota — Wave 7's DB/pool stop is no longer primary.

50u API cgroup was **2.80 / 4.0** (some quota hits; `nr_throttled` 122→135) while health p50 stayed 14 ms. The 100u stop is process CPU at the overlay cap, not connection exhaustion.

Not chosen:

| Candidate | Why not primary |
|-----------|-----------------|
| DB/pool (accept-path writes) | Health (no DB) **did** double. Wave 7 used the opposite (health 14→20, API 1.9–2.7 / 4.0) to name DB/pool. Idle-in-transaction (15/24) is still visible; it is concurrent wait, not the 100u stop. |
| pool budget vs `max_connections` | No HTTP 500 / `TooManyConnections`. Activity 36/58 of 100, not at cap. |
| relay/publish | Unpublished 3/259 mid-hold then 0 after. Relays were busy at 100u (48% / 18%), not idle-while-unpublished-grows as Clock A. |
| workers | Kafka pending lag **held** (Clock B). One assigned consumer ~70–73% CPU. That did not stop Clock A (HTTP 202). |
| still client (wait 0.1–0.5) | Hunt used wait=0. RPS≈users/mean_RT is expected for closed-loop; stop was p50×2 + outbound RPS plateau + API quota peg. |

Do **not** treat `--scale`, `max_connections++`, prefetch inflation, or another overlay CPU bump as the fix.

## k6 `ramping-arrival-rate` — skipped

`make load-k6-grafana` is **constant VUs** (`K6_VUS` / `K6_DURATION` in `scripts/load_k6_grafana.sh` + `load/k6/outbound_ingest.js` `options.vus` / `duration`). It does not implement k6 `ramping-arrival-rate`. Scheduled arrival vs achieved RPS was **not** measured.

Ramping hunt skipped. Thresholds stay enabled; do not pass `--no-thresholds`.

## What this did **not** prove

- Spec §8.1 Stage 1/2/3 throughput or p95 delivery overhead
- Inbound HMAC/argon2 path (not in this Locust mix)
- Partner webhook SLA / `first_success_at` under production Kafka RF
- Full Clock B drain at the new accept rate (lag held; 1-partition workers)
- Isolating remaining API CPU (JSON/Pydantic) vs INSERT/COMMIT on the accept path
- Open-loop arrival (Locust is closed-loop even at wait=0)

## Related

- Wave 4 before-picture: [`ceiling-prodlike.md`](./ceiling-prodlike.md)
- Wave 6 remesure: [`ceiling-remeasure.md`](./ceiling-remeasure.md)
- Wave 7 remesure: [`ceiling-accept-path.md`](./ceiling-accept-path.md)
- Overlay knobs and how-to-hunt: [`docs/runbooks/load-testing.md`](../runbooks/load-testing.md)
- Accept-path smoke (default stack, wait 0.1–0.5): [`locust-smoke.md`](./locust-smoke.md)
- Plan: [`docs/plans/2026-06-12-accept-path-db-opt.md`](../plans/2026-06-12-accept-path-db-opt.md)
