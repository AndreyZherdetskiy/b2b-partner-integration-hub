# Persist CTE remesure (same overlay)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild images, bring up the **same** Wave 8 overlay (`make perf-up`), re-run quality gates and Locust wait=0 Clock A/B after the outbound persist CTE (one statement: INSERT deliveries RETURNING + INSERT outbox). Optional k6 persist-path regression. Tear the stack down. Do not declare spec §8.1 NFR from laptop RPS.

**Architecture:** Accept path now executes one data-modifying CTE then `commit()` (no `flush()` + second INSERT). Overlay unchanged: uvicorn `--workers 4`, `hub-api` `cpus: "4.0"`, `OTEL_SDK_DISABLED=true`, `--scale hub-outbound-worker=2 --scale hub-outbox-relay=2`. Prefetch / `max_connections` unchanged.

**Tech stack:** existing Locust harness, `make load-k6` (constant VUs), Compose overlay, SQL Clock B.

## Global constraints

- SoT: `spec.md` v3.1 EN + ADR 001–010 + `AGENTS.md`.
- **Do not commit. Not Stage Done.** No port shifts. No `down -v`. No prune.
- Same overlay as Wave 8. Do **not** raise `--scale`, Postgres `max_connections`, overlay CPU, or Kafka prefetch.
- Locust mix: outbound accept `POST /internal/v1/outbound/events` → 202. `LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0`. `LOAD_LOCUST_OTEL` unset. Do not empty `REDIS_URL`.
- Scripts do not `source .env`; `set -a && source .env && set +a` in the shell.
- `make load-k6-grafana` is constant VUs — **skip** `ramping-arrival-rate`. No `--no-thresholds`.
- Laptop RPS = facts only. Do **not** rewrite spec §8.1 table (100/400/2000).
- Frozen ports. Project `b2b-partner-integration-hub`.
- English docs. No `Task N` in `app/` / Compose / scripts.
- Implementer ≠ Reviewer. This remesure is **read-only** on `app/` (docs + live measure only).
- After hunt: `make stack-down` (includes perf overlay). Confirm empty ps. No `-v`.

## Git vs gitignore

Tracked: remesure doc, spec §8.1 footnote facts, runbook pointer, `AGENTS.md` §10.5. Ignored: `.local/`, `.env`, `.superpowers/`.

---

### Task 1: quality gates (no stack)

**Files:** none (run only).

- [ ] **Step 1:** `make ci`
- [ ] **Step 2:** `make load-harness`

**Acceptance:** both exit 0.

---

### Task 2: live remesure (read-only `app/`)

**Files:**
- Create: `docs/perf/ceiling-persist-cte.md`
- Modify: `spec.md` §8.1 **footnote only**
- Modify: `docs/runbooks/load-testing.md` (link remesure)
- Modify: `docs/perf/README.md`
- Modify: `AGENTS.md` §10.5 one line

**Forbidden:** `app/` edits, extra workers, CPU quota, `max_connections`, git commit.

1. Frozen ports free. Sibling numbered stacks: `docker compose -p <name> down` only (no `-v`).
2. `make perf-up` then `make seed`. Inspect NanoCpus 4.0, `--workers 4`, scale 2/2, `OTEL_SDK_DISABLED=true`, new `hub-api` image vs Wave 8 `5562fff622ea`.
3. `set -a && source .env && set +a`. Unset `LOAD_LOCUST_OTEL`.
4. Locust wait=0, 50u/60s then 100u/60s. Compare Clock A to Wave 8 (`docs/perf/ceiling-db-roundtrip.md`: 50u outbound ~402 RPS p50 85 ms; 100u ~418 RPS p50 180 ms).
5. Clock B mid-run and after: unpublished, deliveries by status, `pg_stat_activity` (idle-in-transaction), `max_connections`, `docker stats` / API cgroup. Name **one** limiter.
6. `make load-k6` persist-path regression (`K6_VUS=2` `K6_DURATION=10s`, partner from seed/preflight). Skip k6 ramping.
7. Write remesure doc. Footnote facts. `make stack-down`. Empty ps. No `-v`.

**Acceptance:** remesure doc; one named limiter; §8.1 table unchanged; stack down; k6 p95 guard recorded.
