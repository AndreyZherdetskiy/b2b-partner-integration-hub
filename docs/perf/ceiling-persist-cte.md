# Prod-like overlay remesure (persist CTE)

Laptop remesure of the **same** non-default overlay as Wave 6–8 (`docker-compose.perf.yml` + `make perf-up`), after outbound accept persist became one PostgreSQL data-modifying CTE (`INSERT deliveries … RETURNING` then `INSERT outbox_events` in a single statement; `aggregate_id` remains BIGINT `deliveries.id`). Images were rebuilt (`--build`) so containers contain that code.

Success on Clock A is HTTP **202** (`status: accepted`) on `POST /internal/v1/outbound/events`. Clock B is delivery drain in PostgreSQL and Kafka consumer lag.

These numbers are **not** spec §8.1 NFR (100 / 400 / 2000 req/s). Do not copy them into the §8.1 table. Wave 8 before-picture: [`ceiling-db-roundtrip.md`](./ceiling-db-roundtrip.md).

## Overlay (inspected live after rebuild)

| Item | Observed |
|------|----------|
| Command | `make perf-up` (`--build`) then `make seed` |
| `hub-api` image | `9baf962af590` created `2026-04-04T19:24:48Z` (Wave 8 remesure image was `5562fff622ea`) |
| `hub-api` Cmd | `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 4` |
| `OTEL_SDK_DISABLED` on API | `true` |
| `hub-api` CPU quota | `HostConfig.NanoCpus=4000000000` (4.0) |
| `hub-api` memory cap | 512 MiB (unchanged; ~410–417 MiB during holds) |
| `hub-outbound-worker` | 2 replicas |
| `hub-outbox-relay` | 2 replicas |
| Compose project | `b2b-partner-integration-hub` |
| Partner | `acme-erp` (`make seed`) |
| Redis | `REDIS_URL` left set (circuit breaker). Not emptied. |
| Locust OTEL | `LOAD_LOCUST_OTEL` unset |

Default `make stack-up` stays 1 process per service. Overlay is not default. Prefetch / `max_connections` / `--scale` / overlay CPU were not changed for this hunt.

Frozen host ports were free; no sibling numbered `_real_projects` stack was torn down. Volume still held leftover Wave 8 rows (`deliveries` delivered 112950 / pending 27735 at baseline).

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

Stop signal used: **p50 × 2** on outbound accept vs the 50-user hold, health p50 also doubled, API docker stats ~400% of one CPU (fail% stayed 0; no 5xx; outbound RPS did not scale with users). Artifacts: `.local/locust/smoke_stats.csv` (gitignored); copies `cte_50u_stats.csv` / `cte_100u_stats.csv`.

## Clock A (accept) — Wave 8 vs persist CTE

CSV columns: `# reqs`, RPS, fail%, p50/p99. Date: 2026-04-04. Wave 8 figures from [`ceiling-db-roundtrip.md`](./ceiling-db-roundtrip.md).

### 50 users, spawn 25/s, 60s, wait=0

| Name | Wave 8 # reqs | Wave 8 RPS | Wave 8 p50 / p99 (ms) | CTE # reqs | CTE RPS | CTE Fail % | CTE p50 / p99 (ms) |
|------|---------------|------------|------------------------|------------|---------|------------|---------------------|
| `GET /inbound/v1/health` | 8122 | 137.28 | 14 / 110 | 7336 | 124.18 | 0 | 16 / 110 |
| `POST /internal/v1/outbound/events` | 23811 | 402.46 | 85 / 440 | 22041 | 373.08 | 0 | 100 / 370 |
| **Aggregated** | **31933** | **539.74** | **46 / 600** | **29377** | **497.26** | **0** | **87 / 350** |

Closed-loop check: 50 / 0.123 s mean ≈ 406 RPS vs observed 373 — still client-shaped. Doubled users.

Outbound accept ~0.93× Wave 8 RPS (373 vs 402); p50 85 → 100 ms. Health p50 14 → 16 ms (no DB). Laptop noise; not a Clock A unlock.

### 100 users, spawn 50/s, 60s, wait=0 (stop)

| Name | Wave 8 # reqs | Wave 8 RPS | Wave 8 p50 / p99 (ms) | CTE # reqs | CTE RPS | CTE Fail % | CTE p50 / p99 (ms) |
|------|---------------|------------|------------------------|------------|---------|------------|---------------------|
| `GET /inbound/v1/health` | 8330 | 140.57 | 32 / 160 | 8267 | 139.84 | 0 | 31 / 140 |
| `POST /internal/v1/outbound/events` | 24789 | 418.31 | 180 / 580 | 24882 | 420.89 | 0 | 200 / 550 |
| **Aggregated** | **33119** | **558.88** | **150 / 790** | **33149** | **560.73** | **0** | **170 / 520** |

Outbound p50 100 → 200 ms (**×2.00** vs this remesure's 50-user hold). Outbound RPS 373 → 421 (users ×2 did not raise throughput). Health p50 16 → 31 ms (**×1.94**).

Outbound accept ≈ Wave 8 RPS at 100 users (421 vs 418); p50 180 → 200 ms.

## Clock B (delivery)

SQL via `docker compose -p b2b-partner-integration-hub exec -T postgres psql -U hub -d hub`. Baseline before hunt: unpublished 0, `deliveries` delivered 112950 / pending 27735 / retrying 1 (leftover on the volume), `pg_stat_activity` 8, idle-in-transaction 0, `max_connections` 100.

CPU uses `docker stats --no-stream`.

### Mid-run

| Probe | 50 users (during 60s hold) | 100 users (during 60s hold) |
|-------|----------------------------|-----------------------------|
| `outbox_events` unpublished (`published_at IS NULL`) | 349 | 418 |
| `deliveries` | delivered 114399, pending 35740 | delivered 138051, pending 44904 |
| `pg_stat_activity` count | 39 | 63 |
| `max_connections` | 100 | 100 |
| idle in transaction | 12 | 18 |
| Kafka `hub.outbound.pending` lag | 9986 | 19287 |
| `hub-api` CPU | docker **402.17%** | docker **397.79%** |
| `postgres` docker stats | 88.13% | 160.89% |
| `kafka` docker stats | 39.33% | 21.31% |
| `hub-outbox-relay` | 13.62% / 11.38% | 9.25% / 21.49% |
| `hub-outbound-worker` | 68.81% / 0.28% | 71.45% / 0.28% |

Kafka topics are **1 partition** (`infra/kafka/create-topics.sh`). Consumer group `hub-outbound-worker` therefore assigns `hub.outbound.pending` to **one** member — replica 2 stays near 0% CPU. That is Clock B topology, not the Clock A stop.

Pending lag **held** through the accept holds because accept RPS still exceeds the single assigned worker.

### After (drain)

Unpublished returned to **0** after both 60s holds. Deliveries and Kafka lag were **still draining** when probed:

| After | unpublished | deliveries | pending lag | notes |
|-------|-------------|------------|-------------|-------|
| 50-user 60s hold | 0 | delivered 118349, pending 44664 | 16776 | worker-1 draining |
| 100-user 60s hold | 0 | delivered 141284, pending 47026 | 19136 | worker-1 still draining; replica 2 idle |

## Named limiter

**API/process** (uvicorn still 4 workers; they peg Compose `cpus: "4.0"` on `hub-api` again).

Plan-table match: outbound p50 ×2.00 with RPS plateau; fail% 0; health (no DB) p50 16→31 ms (**×1.94**). 100u API docker stats **397.79%**. Unpublished did not accumulate as the Clock A backlog (349/418 mid-hold, **0 after**).

The persist CTE is in the accept path. This remesure did **not** move Clock A off the 4.0 API quota vs Wave 8. 50u outbound RPS was slightly lower than Wave 8 (402 → 373); 100u matched (418 → 421). Remaining accept cost is still process CPU (JSON/Pydantic/INSERT/COMMIT) under four workers, not a second DB round-trip.

Not chosen:

| Candidate | Why not primary |
|-----------|-----------------|
| DB/pool (accept-path writes) | Health (no DB) **did** double. Wave 7 used the opposite (health 14→20, API not pegged) to name DB/pool. Idle-in-transaction (12/18) is concurrent wait, not the 100u stop. Postgres docker stats rose at 100u (88% → 161%) while API stayed at ~400%. |
| pool budget vs `max_connections` | No HTTP 500 / `TooManyConnections`. Activity 39/63 of 100, not at cap. |
| relay/publish | Unpublished 349/418 mid-hold then 0 after. Relays were busy, not idle-while-unpublished-grows as Clock A. |
| workers | Kafka pending lag **held** (Clock B). One assigned consumer ~69–71% CPU. That did not stop Clock A (HTTP 202). |
| still client (wait 0.1–0.5) | Hunt used wait=0. RPS≈users/mean_RT is expected for closed-loop; stop was p50×2 + outbound RPS plateau + API quota peg. |

Do **not** treat `--scale`, `max_connections++`, prefetch inflation, or another overlay CPU bump as the fix.

## k6 persist-path regression

`make load-k6` bind-mount of `load/k6` into `grafana/k6` failed on this WSL host (`moduleSpecifier "/scripts/outbound_ingest.js" couldn't be found`). Re-ran with stdin (`k6 run - < load/k6/outbound_ingest.js`), same as `scripts/load_k6_grafana.sh`. Demo `ADMIN_BOOTSTRAP_TOKEN` from `.env.example`. Partner `acme-erp` `public_id` from Postgres after seed.

| Date | VUs | Duration | HTTP 202 | p95 `http_req_duration{expected_response:true}` | `p(95)<2000` |
|------|-----|----------|----------|--------------------------------------------------|--------------|
| 2026-04-04 | 2 | 10s | 2252/2252 | 11.82 ms | yes |

This is a persist-path guard, not Clock A ceiling and not spec §8.1.

## k6 `ramping-arrival-rate` — skipped

`make load-k6-grafana` is **constant VUs**. It does not implement k6 `ramping-arrival-rate`. Scheduled arrival vs achieved RPS was **not** measured. No `--no-thresholds`.

## Quality gates (same session, before overlay)

`make ci`: ruff, mypy, **445** unit, **7** contract. `make load-harness`: **34** passed + `locust --list` (`HubOutboundUser`).

## What this did **not** prove

- Spec §8.1 Stage 1/2/3 throughput or p95 delivery overhead
- Inbound HMAC/argon2 path (not in this Locust mix)
- Partner webhook SLA / `first_success_at` under production Kafka RF
- Full Clock B drain at the accept rate (lag held; 1-partition workers)
- Isolating remaining API CPU (JSON/Pydantic) vs INSERT/COMMIT on the accept path
- Open-loop arrival (Locust is closed-loop even at wait=0)

## Related

- Wave 8 remesure: [`ceiling-db-roundtrip.md`](./ceiling-db-roundtrip.md)
- Wave 7 remesure: [`ceiling-accept-path.md`](./ceiling-accept-path.md)
- Overlay knobs and how-to-hunt: [`docs/runbooks/load-testing.md`](../runbooks/load-testing.md)
- Plan: [`docs/plans/2026-04-04-persist-cte-remeasure.md`](../plans/2026-04-04-persist-cte-remeasure.md)
