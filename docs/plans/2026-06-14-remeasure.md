# Wave 6 — rebuild + remesure (same overlay)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild app images, bring up the **same** prod-like overlay (`make perf-up`), repeat Locust wait=0 Clock A + Clock B, **reclassify** the limiter, write honest facts, tear the stack down. Do not declare spec §8.1 NFR from laptop RPS.

**Architecture:** Wave 5 shipped (1) compiled JSON Schema validator reuse, (2) process-wide `get_sessionmaker` for workers/relay, (3) overlay `hub-api` `cpus: "4.0"`. Images **must** be rebuilt (`--build`) so containers contain that code. Overlay still: uvicorn `--workers 4`, `OTEL_SDK_DISABLED=true`, `--scale hub-outbound-worker=2 --scale hub-outbox-relay=2`. Prefetch unchanged.

**Tech stack:** existing Locust harness, optional skip of k6 ramping (constant-VU Grafana runner), Compose overlay, SQL Clock B.

## Global constraints

- SoT: `spec.md` v3.1 EN + ADR 001–010 + `AGENTS.md`.
- **Do not commit. Not Stage Done.** No port shifts. No `down -v`. No prune.
- Same overlay as Wave 5 Task 2. Do **not** raise `--scale`, Postgres `max_connections`, or Kafka prefetch as the “fix”.
- Locust mix: outbound accept `POST /internal/v1/outbound/events` → 202. `LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0`. `LOAD_LOCUST_OTEL` unset. Do not empty `REDIS_URL`.
- Scripts do not `source .env`; `set -a && source .env && set +a` in the shell.
- Scheduled k6 arrival ≠ achieved RPS. `make load-k6-grafana` is constant VUs — **document skip** of `ramping-arrival-rate` unless you add a separate script. No `--no-thresholds`.
- Laptop RPS = facts only. Do **not** rewrite spec §8.1 table (100/500/2000).
- Frozen ports. Project `b2b-partner-integration-hub`.
- English docs. No `Task N` in `app/`/Compose/scripts.
- Implementer ≠ Reviewer. This wave is **read-only** on `app/` (docs + live measure only).
- After hunt: `make stack-down` (includes perf overlay). Confirm empty ps. No `-v`.

## Git vs gitignore

Tracked: `docs/perf/ceiling-remeasure.md` (or update `docs/perf/ceiling-prodlike.md` plus a dated remesure section), spec §8.1 footnote facts, runbook pointer, `AGENTS.md` §10.5. Ignored: `.local/`, `.env`, `.superpowers/`.

---

### Task 1: live remesure (read-only `app/`)

**Files:**
- Create or extend: `docs/perf/ceiling-remeasure.md` (preferred new file so Wave 4 hunt stays the before picture)
- Modify: `spec.md` §8.1 **footnote only** (add remesure facts; do not change table numbers)
- Modify: `docs/runbooks/load-testing.md` (link remesure)
- Modify: `AGENTS.md` §10.5 one line

**Forbidden:** `app/` edits, pool sizes, prefetch, extra `--scale`, `max_connections++`, git commit.

**Procedure:**

1. If needed, `docker compose -p <other-numbered-project> down` only. Then:

```bash
make perf-up
make seed
```

Inspect:

- `hub-api` Cmd: `--factory` and `--workers 4`
- `OTEL_SDK_DISABLED=true`
- 2× `hub-outbound-worker`, 2× `hub-outbox-relay`
- `hub-api` CPU quota **4.0** if visible (`docker inspect` NanoCpus / HostConfig.NanoCpus, or `docker stats` no longer pegged at ~100% of 1 CPU while host has idle cores)
- Image built after Wave 5 (new image id vs pre-rebuild if you recorded one; otherwise `--build` in `perf-up` is enough)

2. Export env: `set -a && source .env && set +a`. Unset `LOAD_LOCUST_OTEL`.

```bash
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=50 LOAD_SPAWN_RATE=25 LOAD_RUN_TIME=60s make load-locust
```

3. **Clock A:** Locust CSV `# reqs`, RPS, fail%, p50/p99 for outbound **and** health. Compare to Wave 4 (`docs/perf/ceiling-prodlike.md`: ~57 RPS / p50 690 ms at 50u; ~62 RPS / p50 1400 ms at 100u). If RPS ≈ users/mean_RT, double users (100) until stop (fail%>1, p50×2 vs the 50u hold, CPU peg, 5xx).

4. **Clock B** mid-run and after: unpublished `outbox_events` (`published_at IS NULL`); `deliveries` by status; `pg_stat_activity` vs `SHOW max_connections`; `docker stats` (api vs postgres vs kafka vs relay vs worker). Kafka lag if cheap.

5. Classify **one** primary limiter (may have **moved**):

| Observation | Verdict |
|-------------|---------|
| HTTP 500 TooManyConnections / activity at cap | pool budget vs max_connections |
| Unpublished outbox grows, Kafka thin, workers ~0% CPU | **relay/publish**, not workers |
| Unpublished=0, Kafka lag grows, worker CPU | workers in play (only if lag holds) |
| wait 0.1–0.5 and RPS≈users/wait | still client; wait=0 required |
| API CPU pegged at **4.0** quota, DB idle, unpublished=0 | still API/process (next software/GIL) |
| API CPU **not** pegged, health p50 stays low, outbound p50 climbs, postgres CPU | DB / pool |
| p50 grows, fail% low, docker stats postgres CPU | DB |

6. k6 ramping: **skip** (constant-VU runner) unless you already have a ramping script. No `--no-thresholds`.

7. Write `docs/perf/ceiling-remeasure.md`: overlay, rebuild, commands, Clock A table (before vs after), Clock B, **named limiter**, what was **not** proven.

8. `make stack-down`. Confirm empty `docker compose -p b2b-partner-integration-hub ps`. No `-v`.

**Acceptance:** remesure doc with one named limiter; §8.1 table unchanged; no `app/` diff; stack down; no invented NFR RPS.

---

## Out of Wave 6

Further software waves only if remesure names a **new** limiter. Do not keep raising overlay CPU.
