# Prod-like overlay remesure (isolated Kafka pending drain)

Laptop remesure of the **same** non-default overlay as the persist-CTE hunt (`docker-compose.perf.yml` + `make perf-up`). Goal: start each Locust hold with `hub.outbound.pending` consumer lag **0**, then measure wall-clock drain to lag 0 after Locust stops — so the ~17k / ~19k figures are not leftover Kafka messages from a previous hold.

Success on Clock A is HTTP **202** (`status: accepted`) on `POST /internal/v1/outbound/events`. Clock B is Kafka group lag on `hub.outbound.pending` (and unpublished outbox / `deliveries.status` as supporting probes).

These numbers are **not** spec §8.1 NFR. Persist-CTE before-picture: [`ceiling-persist-cte.md`](./ceiling-persist-cte.md). Plan: [`docs/plans/2026-04-03-kafka-lag-drain-remeasure.md`](../plans/2026-04-03-kafka-lag-drain-remeasure.md).

## Overlay (inspected live after rebuild)

| Item | Observed |
|------|----------|
| Command | `make perf-up` (`--build`) then `make seed` |
| `hub-api` image | `8563e07b6141` created `2026-04-03T20:00:30Z` |
| `hub-api` Cmd | `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 4` |
| `OTEL_SDK_DISABLED` on API | `true` |
| `hub-api` CPU quota | `HostConfig.NanoCpus=4000000000` (4.0) |
| `hub-outbound-worker` | 2 replicas |
| `hub-outbox-relay` | 2 replicas |
| Compose project | `b2b-partner-integration-hub` |
| Partner | `acme-erp` (`make seed`) |
| Redis | `REDIS_URL` left set. Not emptied. |
| Locust OTEL | `LOAD_LOCUST_OTEL` unset |
| k6 | skipped (would dirty pending) |

Kafka broker logs are `/tmp/kraft-combined-logs` with **no** named volume. `stack-down` already drops broker state. Offsets were **not** reset; leftover drain was not needed.

Frozen host ports were free. Tripbox Compose projects were already `exited` (not torn down). Prefetch / `max_connections` / `--scale` / overlay CPU were not changed.

## Isolation baseline (before any Locust)

`kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group hub-outbound-worker`.

| Probe | Value |
|-------|--------|
| `outbox_events` unpublished | **0** |
| `deliveries` | delivered 154864 / pending **35698** / retrying 1 (Postgres volume leftover from earlier hunts) |
| `hub.outbound.pending` | PARTITION 0, CURRENT-OFFSET `-`, LOG-END-OFFSET **0**, LAG `-` |
| Assigned member on pending | worker-1 only (`/172.18.0.14`) |

Postgres `pending` leftover is **not** Kafka lag: those rows were already published in earlier sessions; this broker log is empty. Clock B Kafka numbers below are this session only. After each drain, `deliveries.pending` returned to **35698** — this hunt’s rows reached `delivered`.

## Hunt commands

```bash
set -a && source .env && set +a
unset LOAD_LOCUST_OTEL
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=50 LOAD_SPAWN_RATE=25 LOAD_RUN_TIME=60s make load-locust
# wait until pending lag = 0, then:
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=100 LOAD_SPAWN_RATE=50 LOAD_RUN_TIME=60s make load-locust
```

Drain clock: poll `--describe` until `LAG=0` on `hub.outbound.pending` (≈5 s per sample including `kafka-consumer-groups.sh`). Artifacts (gitignored): `.local/locust/drain_*`.

## Clock A (accept) — CSV SoT

Date: 2026-04-03. Fail % **0**. Persist-CTE figures from [`ceiling-persist-cte.md`](./ceiling-persist-cte.md).

### 50 users, spawn 25/s, 60s, wait=0

| Name | Persist-CTE RPS | Persist-CTE p50 / p99 (ms) | This # reqs | This RPS | This p50 / p99 (ms) |
|------|-----------------|----------------------------|-------------|----------|---------------------|
| `GET /inbound/v1/health` | 124.18 | 16 / 110 | 6929 | 117.21 | 16 / 250 |
| `POST /internal/v1/outbound/events` | 373.08 | 100 / 370 | 20862 | 352.89 | 110 / 490 |
| **Aggregated** | 497.26 | 87 / 350 | 27791 | 470.09 | 74 / 640 |

Outbound accept ~0.95× persist-CTE RPS. Laptop noise; not a Clock A change.

### 100 users, spawn 50/s, 60s, wait=0 (stop)

| Name | Persist-CTE RPS | Persist-CTE p50 / p99 (ms) | This # reqs | This RPS | This p50 / p99 (ms) |
|------|-----------------|----------------------------|-------------|----------|---------------------|
| `GET /inbound/v1/health` | 139.84 | 31 / 140 | 7606 | 128.55 | 33 / 270 |
| `POST /internal/v1/outbound/events` | 420.89 | 200 / 550 | 22641 | 382.65 | 210 / 610 |
| **Aggregated** | 560.73 | 170 / 520 | 30247 | 511.20 | 190 / 810 |

Outbound p50 110 → 210 ms (**×1.91** vs this remesure’s 50-user hold). Health p50 16 → 33 ms (**×2.06**). Outbound RPS 353 → 383 (users ×2 did not double throughput).

## Clock B — isolated lag and drain

`log_end` did **not** grow after Locust shut down (unpublished already 0). Drain is worker HTTP+PG on a frozen topic end.

### 50-user hold (started from LOG-END-OFFSET 0)

| Instant | Wall (UTC) | unpublished | pending lag | current / log_end | notes |
|---------|------------|-------------|-------------|-------------------|--------|
| Mid-hold (~30 s) | 20:02:44 | 4 | **11452** | 1941 / 13393 | API docker **387.96%**; worker-1 **47.37%**; worker-2 **0.27%** |
| First sample after Locust stop | 20:03:41 | 0 | **15739** | 5399 / 21138 | Locust shut down 20:03:14 |
| Drain-clock start | 20:03:45 | — | **15557** | 5581 / 21138 | `log_end` frozen |
| Lag 0 | 20:07:32 | 0 | **0** | 21138 / 21138 | `deliveries.pending` back to 35698 |

| Drain measure | Value |
|---------------|--------|
| Locust shut down → lag 0 | **258 s** (20:03:14 → 20:07:32) |
| First post-stop sample → lag 0 | **225 s** (15557 messages) |
| Implied drain rate | **≈ 69 msg/s** (15557 / 225) |
| Kafka messages this hold | **21138** (`log_end`; Δ delivered 21138) |

### 100-user hold (started from lag 0, `log_end` 21138)

| Instant | Wall (UTC) | unpublished | pending lag | current / log_end | notes |
|---------|------------|-------------|-------------|-------------------|--------|
| Mid-hold (~30 s) | 20:08:34 | 91 | **11599** | 23124 / 34723 | API docker **400.21%**; worker-1 **72.38%**; worker-2 **0.27%** |
| First sample after Locust stop | 20:09:26 | 0 | **17981** | 26188 / 44169 | Locust shut down 20:09:08 |
| Drain-clock start | 20:09:30 | — | **17799** | 26370 / 44169 | `log_end` frozen |
| Lag 0 | 20:13:52 | 0 | **0** | 44169 / 44169 | `deliveries.pending` back to 35698 |

| Drain measure | Value |
|---------------|--------|
| Locust shut down → lag 0 | **284 s** (20:09:08 → 20:13:52) |
| First post-stop sample → lag 0 | **259 s** (17799 messages) |
| Implied drain rate | **≈ 69 msg/s** (17799 / 259) |
| Kafka messages this hold | **23031** (`44169 − 21138`; Δ delivered 23031) |

One partition, one assigned worker on pending (replica 2 kept retry topics at LOG-END 0). That matches persist-CTE Clock B topology.

## What the previous ~17k / ~19k were

Not a previous-session Kafka leftover (broker log is empty after `stack-down`). They were **this-hold surplus**: accept ~350–380 msg/s vs one sequential consumer ~69 msg/s.

The persist-CTE **100-user after-hold lag 19136** was taken **without** draining the 50-user leftover first. This isolated 100-user after-hold lag is **17981** — same order of magnitude. During a stacked 100-user hold the worker keeps draining, so net lag does not become 16k+18k.

Postgres `pending` 35698 at baseline **is** leftover from earlier hunts; it did not appear on `hub.outbound.pending` and did not move during these drains.

## Named limiter

Clock A: **API/process** at Compose `cpus: "4.0"` (health doubled; API ~388–400%). Unchanged vs persist-CTE.

Clock B: one assigned `hub-outbound-worker` on a 1-partition topic, keyed by one Locust partner (`acme-erp`). Drain ≈ **69 msg/s** after accept stops. That is not the Clock A stop.

## Quality gates (this session)

`make load-harness`: **34** passed + `locust --list` (`HubOutboundUser`). `make ci` skipped (no `app/` change).

## What this did **not** prove

- Spec §8.1 Kafka e2e lag P95 (Stage 2 &lt; 2 s / Stage 3 &lt; 1 s)
- Drain rate with 12 partitions or many partner keys
- Inbound HMAC path
- Open-loop arrival

## Related

- Persist-CTE remesure: [`ceiling-persist-cte.md`](./ceiling-persist-cte.md)
- Overlay knobs: [`docs/runbooks/load-testing.md`](../runbooks/load-testing.md)
