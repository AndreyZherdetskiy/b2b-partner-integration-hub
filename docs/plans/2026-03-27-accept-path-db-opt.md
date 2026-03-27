# Wave 8 — accept-path DB round-trips (no workers, no CPU, no max_connections)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut **PostgreSQL round-trips and time-in-transaction** on the outbound accept path (named Wave 7 limiter: DB/pool at overlay `cpus: "4.0"`) using software only, then remesure the **same** overlay.

**Architecture:** Wave 7 moved Clock A off API CPU (health p50 14→20 ms; API ~1.9–2.7/4.0 at 100 users). Accept still does a **SELECT deliveries by source_event_id** on every Locust request even though keys are unique, then INSERT deliveries + outbox + COMMIT. API lifespan engine uses `pool_pre_ping=True` (extra `SELECT 1` per checkout). 100u showed **28 idle-in-transaction**. Software: (1) **insert-first** idempotency — SELECT only after `IntegrityError`; (2) **disable pool_pre_ping** on the API engine (and align `get_sessionmaker`). Do **not** add workers, overlay CPU, `--scale`, or `max_connections`.

**Tech stack:** existing SQLAlchemy IntegrityError path, pytest FakeSession, Locust remesure.

## Global constraints

- SoT: `spec.md` v3.1 EN + ADR 001–010 + `AGENTS.md`.
- **Do not commit. Not Stage Done.** No port shifts. No `down -v`. No prune.
- **No new workers. No overlay CPU bump. No `--scale`. No `max_connections++`. No pool_size++. No prefetch++.**
- Locust mix unchanged. Unique `idempotency_key` per request. Duplicate SELECT stays on the IntegrityError path only.
- Unique constraint remains `(partner_id, idempotency_key)` — do not invent a unique on `source_event_id`.
- English docs. No `Task N` in `app/` / Compose / scripts.
- Implementer ≠ Reviewer. Task 1 TDD. Task 2 live remesure is read-only on `app/` except docs.
- If Clock A is still DB/pool after this wave and remaining cost is INSERT/COMMIT/Pydantic, stop adding software in this wave. Orchestrator may then nudge spec §8.1 **without** a runbook/AGENTS how-to.

## Git vs gitignore

Tracked: enqueue insert-first, engine pool_pre_ping, pin tests, remesure doc, spec footnote facts. Ignored: `.env`, `.venv/`, `.local/`, `.superpowers/`.

---

### Task 1: insert-first idempotency + no pool_pre_ping

**Files:**
- Modify: `app/domain/services/outbound_enqueue.py` — do **not** call `fetch_deliveries_by_source_event_id` before INSERT. On `IntegrityError` after flush: rollback, then SELECT (existing race path). Duplicate HTTP 200 behaviour must stay.
- Modify: `app/main.py` lifespan `create_async_engine` — `pool_pre_ping=False` (keep `expire_on_commit=False`).
- Modify: `app/db/session.py` `get_sessionmaker` — same `pool_pre_ping=False` so workers match.
- Modify: `tests/unit/test_delivery_create.py` FakeSession — after L1 cache miss, execute order is partner then endpoints (no empty deliveries SELECT). Duplicate tests must go through IntegrityError + SELECT (see `IntegrityRetrySession`).
- Modify: `tests/unit/test_endpoint_fanout.py` if its FakeSession assumes a deliveries SELECT slot.
- Create or extend: `tests/unit/test_session.py` or engine pin — `pool_pre_ping is False` on the engine used by `create_app` / `get_sessionmaker`.
- Existing: `tests/unit/test_accept_path_cache.py` — second enqueue still must **not** re-SELECT partner/schema/endpoints; it also must **not** SELECT deliveries on the unique-key success path. Adjust execute-count assertions.

**Forbidden:** overlay CPU, `--scale`, `max_connections`, `pool_size`, live Locust, git commit.

- [ ] **Step 1: RED** — test that `enqueue_outbound_for_event` with a unique key does not `execute` a deliveries SELECT (count partner/endpoints/schema only; or inspect `stmt_targets_table(..., "deliveries")` is False on the success path). Duplicate still returns the existing public ids via IntegrityError + SELECT. Pin `pool_pre_ping is False`.

- [ ] **Step 2: Implement** insert-first + `pool_pre_ping=False`.

- [ ] **Step 3: GREEN** `uv run pytest tests/unit/test_delivery_create.py tests/unit/test_endpoint_fanout.py tests/unit/test_accept_path_cache.py tests/unit/test_schema_registry.py tests/unit/test_session.py -q` (create the session pin file if needed). Autouse cache reset stays.

**Acceptance:** unique-key accept = no deliveries SELECT; duplicate still 200; `pool_pre_ping` False; no overlay changes.

---

### Task 2: remesure same overlay (read-only `app/`)

**Files:**
- Create: `docs/perf/ceiling-db-roundtrip.md`
- Modify: `spec.md` §8.1 **footnote facts only** (do not change the 100/500/2000 table in this task)
- Modify: `docs/runbooks/load-testing.md` link
- Modify: `AGENTS.md` §10.5 one line

**Forbidden:** `app/` edits, extra workers, CPU quota, `max_connections`, git commit.

**Live names (Makefile is SoT):** `make perf-up` then `make seed`. Compose `b2b-partner-integration-hub`. `LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0`. `LOAD_USERS` / `LOAD_SPAWN_RATE` / `LOAD_RUN_TIME`. `make load-locust`. CSV `.local/locust/smoke_stats.csv`. `make stack-down` (no `-v`).

1. Rebuild overlay. Inspect NanoCpus 4.0, `--workers 4`, scale 2/2, `OTEL_SDK_DISABLED=true`.
2. `set -a && source .env && set +a`. Unset `LOAD_LOCUST_OTEL`.
3. Locust wait=0, 50u/60s then 100u/60s. Compare Clock A to Wave 7 (`docs/perf/ceiling-accept-path.md`: 50u outbound ~236 RPS p50 180 ms; 100u ~251 RPS p50 350 ms).
4. Clock B: unpublished, deliveries by status, `pg_stat_activity` (idle-in-transaction), `max_connections`, `docker stats` / API cgroup. Name **one** limiter.
5. Skip k6 ramping. No `--no-thresholds`.
6. Write remesure doc. Footnote facts. `make stack-down`. Empty ps. No `-v`.

**Acceptance:** remesure doc; one named limiter; §8.1 table unchanged in this task; stack down.

---

## Out of Wave 8

If Clock A is still DB/pool, remaining software is INSERT/COMMIT/JSONB/Pydantic. Do **not** add workers or CPU. Orchestrator may then adjust spec numbers **without** a runbook/AGENTS how-to for that adjustment.
