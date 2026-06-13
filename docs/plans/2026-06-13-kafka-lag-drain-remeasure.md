# Kafka pending-lag drain remesure (same overlay)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring Kafka consumer lag on `hub.outbound.pending` to **0**, re-run Locust wait=0 Clock A/B on the **same** `make perf-up` overlay, and measure wall-clock drain to lag=0 after each hold stops — so the ~17k/~19k figures are not leftover from a previous hold.

**Architecture:** Kafka Compose logs live in `/tmp/kraft-combined-logs` with **no** named volume — `stack-down` already drops broker state. Isolation for this hunt is: confirm lag=0 before each hold, then poll lag after Locust exits until 0. Do **not** `--reset-offsets` unless natural drain of leftover (if any) stalls; skip would hide worker throughput. Do **not** set `outbox_events.published_at` by hand. Overlay unchanged (no extra workers, CPU, `max_connections`, prefetch).

**Tech stack:** existing Locust harness, Compose overlay, `kafka-consumer-groups.sh --describe --group hub-outbound-worker`, SQL Clock B.

## Global constraints

- SoT: `spec.md` v3.1 EN + ADR 001–010 + `AGENTS.md`.
- **Do not commit. Not Stage Done.** No port shifts. No `down -v`. No prune.
- Same overlay as persist-CTE remesure. Do **not** raise `--scale`, Postgres `max_connections`, overlay CPU, or Kafka prefetch.
- Locust mix: outbound accept `POST /internal/v1/outbound/events` → 202. `LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0`. `LOAD_LOCUST_OTEL` unset. Do not empty `REDIS_URL`.
- Scripts do not `source .env`; `set -a && source .env && set +a` in the shell.
- Skip k6 this hunt (it would dirty pending). Skip `make ci` (no `app/` change).
- Laptop RPS = facts only. Do **not** rewrite spec §8.1 table (100/400/2000).
- Frozen ports. Project `b2b-partner-integration-hub`.
- English docs. No `Task N` in `app/` / Compose / scripts.
- After hunt: `make stack-down` (includes perf overlay). Confirm empty ps. No `-v`.

## Git vs gitignore

Tracked: remesure doc, spec §8.1 **footnote only**, runbook pointer, `AGENTS.md` §10.5. Ignored: `.local/`, `.env`, `.superpowers/`.

---

### Task 1: live isolated drain remesure (read-only `app/`)

**Files:**
- Create: `docs/perf/ceiling-kafka-lag-drain.md`
- Modify: `spec.md` §8.1 footnote only
- Modify: `docs/runbooks/load-testing.md`
- Modify: `docs/perf/README.md`
- Modify: `AGENTS.md` §10.5 one line

**Forbidden:** `app/` edits, extra workers, CPU quota, `max_connections`, offset reset unless leftover drain stalls, git commit.

1. Frozen ports free. Sibling numbered stacks: `docker compose -p <name> down` only (no `-v`).
2. `make load-harness`. `make perf-up` then `make seed`. Inspect overlay knobs unchanged.
3. Baseline: unpublished, `deliveries` by status, `hub.outbound.pending` lag. Wait until lag is **0** (natural consume). Snapshot PG counts (volume leftover pending is **not** Kafka lag).
4. Locust wait=0, 50u/60s. Mid-run Clock B + lag. On `make load-locust` exit: record lag T0, unpublished, then poll lag every 2s until 0 or 15 min. Record wall seconds and implied drain msg/s.
5. Confirm lag=0. Repeat 100u/60s the same way (do **not** start 100u on a leftover backlog).
6. Write remesure doc. Footnote drain facts. `make stack-down`. Empty ps. No `-v`.

**Acceptance:** remesure doc with lag=0 before each hold, lag at Locust stop, drain seconds to 0 (or timeout evidence); §8.1 table unchanged; stack down.

Live 2026-06-13: lag=0 baseline; 50u after-stop lag 15739 → 0 in 258 s; 100u 17981 → 0 in 284 s; ≈69 msg/s. Evidence: [`docs/perf/ceiling-kafka-lag-drain.md`](../perf/ceiling-kafka-lag-drain.md).
