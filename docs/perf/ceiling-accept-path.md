# Prod-like overlay remesure (Wave 7)

Laptop remesure of the **same** non-default overlay as Wave 6 (`docker-compose.perf.yml` + `make perf-up`), after Wave 7 software (pure ASGI correlation + max-body middleware; process-local TTL L1 cache on partner / schema / endpoints for the outbound accept path). Images were rebuilt (`--build`) so containers contain that code.

Success on Clock A is HTTP **202** (`status: accepted`) on `POST /internal/v1/outbound/events`. Clock B is delivery drain in PostgreSQL and Kafka consumer lag.

These numbers are **not** spec §8.1 NFR (100 / 500 / 2000 req/s). Do not copy them into the §8.1 table. Wave 6 before-picture: [`ceiling-remeasure.md`](./ceiling-remeasure.md).

## Overlay (inspected live after rebuild)

| Item | Observed |
|------|----------|
| Command | `make perf-up` (`--build`) then `make seed` |
| `hub-api` image | `bf53722e2b72` created `2026-06-12T18:05:23Z` (Wave 6 remesure image was `45112fde3feb`) |
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
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=100 LOAD_SPAWN_RATE=50 LOAD_RUN_TIME=60s make load-locust
```

Stop signal used: **p50 × 2** on outbound accept vs the 50-user hold (fail% stayed 0; no 5xx; outbound RPS did not scale with users). Artifacts: `.local/locust/smoke_stats.csv` (gitignored).

A second 100-user hold was run only to recover `docker stats --no-stream` CPU (the first two holds reported ~0.6–0.8% on `hub-api` while Locust was at 230+ RPS — WSL/cgroup accounting). Clock A tables below are the prescribed 50u then first 100u sequence.

## Clock A (accept) — Wave 6 vs Wave 7

CSV columns: `# reqs`, RPS, fail%, p50/p99. Date: 2026-06-12. Wave 6 figures from [`ceiling-remeasure.md`](./ceiling-remeasure.md).

### 50 users, spawn 25/s, 60s, wait=0

| Name | Wave 6 # reqs | Wave 6 RPS | Wave 6 p50 / p99 (ms) | Wave 7 # reqs | Wave 7 RPS | Wave 7 Fail % | Wave 7 p50 / p99 (ms) |
|------|---------------|------------|------------------------|---------------|------------|---------------|-------------------------|
| `GET /inbound/v1/health` | 4602 | 77.98 | 66 / 190 | 4554 | 77.03 | 0 | 14 / 93 |
| `POST /internal/v1/outbound/events` | 13804 | 233.89 | 170 / 510 | 13962 | 236.17 | 0 | 180 / 490 |
| **Aggregated** | **18406** | **311.87** | **140 / 480** | **18516** | **313.21** | **0** | **150 / 470** |

Closed-loop check: 50 / 0.156 s mean ≈ 321 RPS vs observed 313.21 — still client-shaped. Doubled users.

Outbound accept ≈ Wave 6 RPS (236 vs 234); p50 170 → 180 ms (noise, not a win). Health p50 66 → 14 ms (pure ASGI + async health; no DB).

### 100 users, spawn 50/s, 60s, wait=0 (stop)

| Name | Wave 6 # reqs | Wave 6 RPS | Wave 6 p50 / p99 (ms) | Wave 7 # reqs | Wave 7 RPS | Wave 7 Fail % | Wave 7 p50 / p99 (ms) |
|------|---------------|------------|------------------------|---------------|------------|---------------|-------------------------|
| `GET /inbound/v1/health` | 4314 | 73.12 | 160 / 350 | 4949 | 83.60 | 0 | 20 / 110 |
| `POST /internal/v1/outbound/events` | 12923 | 219.05 | 380 / 790 | 14836 | 250.60 | 0 | 350 / 890 |
| **Aggregated** | **17237** | **292.17** | **340 / 770** | **19785** | **334.20** | **0** | **290 / 860** |

Outbound p50 180 → 350 ms (**×1.94** vs this remesure's 50-user hold). Outbound RPS 236 → 251 (users ×2 did not raise throughput). Health p50 14 → 20 ms (**×1.43**, not doubled).

Outbound accept ~1.14× Wave 6 RPS at 100 users; p50 380 → 350 ms.

## Clock B (delivery)

SQL via `docker compose -p b2b-partner-integration-hub exec -T postgres psql -U hub -d hub`. Baseline before hunt: unpublished 0, `deliveries` all `delivered` (46989 leftover on the volume), `pg_stat_activity` 8, `max_connections` 100, Kafka `hub.outbound.pending` lag unset (no offsets yet).

`docker stats --no-stream` during the 50u hold and the first 100u hold reported `hub-api` **0.6–0.8% CPU** while Locust outbound was 230–250 RPS. That sample is discarded as accounting noise. CPU below uses `cpu.stat` (`usage_usec` delta) on the API cgroup, plus a repeat 100u `docker stats --no-stream` that returned plausible percentages.

### Mid-run

| Probe | 50 users (during 60s hold) | 100 users (during 60s hold) |
|-------|----------------------------|-----------------------------|
| `outbox_events` unpublished (`published_at IS NULL`) | 8 | 42 |
| `deliveries` | delivered 49029, pending 12043 | delivered 62391, pending 12718 |
| `pg_stat_activity` count | 29 | 69 |
| `max_connections` | 100 | 100 |
| idle in transaction | 1 | 28 |
| Kafka `hub.outbound.pending` lag | 11843 | 13589 |
| `hub-api` CPU | cgroup ≈ **4.0 cores** (quota peg; `nr_throttled` 40 after the hold) | cgroup **1.94 cores / 4.0** over 13.4 s (not pegged) |
| `postgres` docker stats / cgroup | 11.04% (docker; API docker discarded) | docker 12.56% on first hold; cgroup ~16% over 2 s |
| `kafka` docker stats | 10.28% | 7.11% |
| `hub-outbox-relay` | 0.22% / 0.27% | 0.30% / 0.34% |
| `hub-outbound-worker` | 0.29% / 64.68% | 0.19% / 65.01% |

Repeat 100u `docker stats --no-stream` (CPU only; Clock A not taken from this hold): `hub-api` 304.53%, `postgres` 854.14%, `kafka` 146.37%, worker-2 40.66%, relays 12.89% / 20.36%. API cgroup over that 18.1 s window: **2.67 cores / 4.0**. Postgres cgroup over the following 2 s: ~20%. The 854% postgres docker figure is treated as a long-window artifact; cgroup is the short sample.

Kafka topics are **1 partition** (`infra/kafka/create-topics.sh`). Consumer group `hub-outbound-worker` therefore assigns `hub.outbound.pending` to **one** member — replica 1 stays near 0% CPU. That is Clock B topology, not the Clock A stop.

Pending lag **held** through the accept holds (thousands) because accept RPS still exceeds the single assigned worker.

### After (drain)

Unpublished returned to 0 after both 60s holds. Deliveries and Kafka lag were **still draining** when probed (not fully caught up in the hunt window):

| After | unpublished | deliveries | pending lag | notes |
|-------|-------------|------------|-------------|-------|
| 50-user 60s hold | 0 | delivered 60775, pending 344 | 115 | worker-2 draining; lag almost cleared before 100u |
| 100-user 60s hold | 0 | delivered 70521, pending 5656 | 5442 | worker-2 still draining; API idle |

## Named limiter

**DB/pool (accept-path writes)** (uvicorn still 4 workers; Compose `cpus: "4.0"` on `hub-api` is **not** the 100-user stop).

Plan-table match: outbound p50 ×1.94 with RPS plateau; fail% 0; unpublished did not accumulate as the Clock A backlog (8/42 mid-hold, 0 after). Health (no DB) p50 stayed 14→20 ms. API cgroup at 100 users was **1.9–2.7 cores of a 4.0 quota**, not pegged. `pg_stat_activity` 69 with **28 idle in transaction**.

The limiter **class moved**. Wave 7 software did not raise 50u outbound RPS (234 → 236) and did not cut 50u outbound p50 (170 → 180). It did cut health p50 (66 → 14 ms) and raised 100u outbound RPS (219 → 251) with a slightly better outbound p50 (380 → 350). The 100u stop is wait on accept-path SQL / session pool, not API quota.

50u API cgroup still shows quota throttling (`nr_throttled` 40). Remaining process CPU exists; it is not what stopped Clock A when users doubled.

Not chosen:

| Candidate | Why not primary |
|-----------|-----------------|
| API/process at 4.0 quota | Health (no DB) did not double. 100u API cgroup 1.9–2.7 / 4.0, not pegged. Wave 6's smoking gun is gone. |
| pool budget vs `max_connections` | No HTTP 500 / `TooManyConnections`. Activity 69/100, not at cap. Idle-in-transaction is the wait signal, not connection exhaustion. |
| relay/publish | Unpublished 8/42 mid-hold then 0 after. Relays were not idle-while-unpublished-grows on the first holds. |
| workers | Kafka pending lag **held** (Clock B). One assigned consumer ~40–65% CPU. That did not stop Clock A (HTTP 202). |
| still client (wait 0.1–0.5) | Hunt used wait=0. RPS≈users/mean_RT is expected for closed-loop; stop was p50×2 + outbound RPS plateau. |

Do **not** treat `--scale`, `max_connections++`, prefetch inflation, or another overlay CPU bump as the fix.

## k6 `ramping-arrival-rate` — skipped

`make load-k6-grafana` is **constant VUs** (`K6_VUS` / `K6_DURATION` in `scripts/load_k6_grafana.sh` + `load/k6/outbound_ingest.js` `options.vus` / `duration`). It does not implement k6 `ramping-arrival-rate`. Scheduled arrival vs achieved RPS was **not** measured.

Ramping hunt skipped. Thresholds stay enabled; do not pass `--no-thresholds`.

## What this did **not** prove

- Spec §8.1 Stage 1/2/3 throughput or p95 delivery overhead
- Inbound HMAC/argon2 path (not in this Locust mix)
- Partner webhook SLA / `first_success_at` under production Kafka RF
- Full Clock B drain at the new accept rate (lag held; 1-partition workers)
- Isolating pool wait vs INSERT time vs JSON/Pydantic on the accept path
- Open-loop arrival (Locust is closed-loop even at wait=0)
- That Wave 7 software raised 50u outbound RPS (it did not)

## Related

- Wave 4 before-picture: [`ceiling-prodlike.md`](./ceiling-prodlike.md)
- Wave 6 remesure: [`ceiling-remeasure.md`](./ceiling-remeasure.md)
- Overlay knobs and how-to-hunt: [`docs/runbooks/load-testing.md`](../runbooks/load-testing.md)
- Accept-path smoke (default stack, wait 0.1–0.5): [`locust-smoke.md`](./locust-smoke.md)
- Plan: [`docs/plans/2026-06-12-accept-path-opt.md`](../plans/2026-06-12-accept-path-opt.md)
