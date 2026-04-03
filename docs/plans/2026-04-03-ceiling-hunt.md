# Prod-like overlay + ceiling hunt (Wave 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a **non-default** Compose overlay that looks like production process topology (uvicorn workers + competing Kafka/outbox consumers), then run a **read-only** ceiling hunt with Locust `wait=0` and name **one** limiter with evidence. Do **not** change product code. Do **not** rewrite spec §8.1 NFR from laptop RPS.

**Architecture:** Default `docker-compose.yml` stays 1 process per service. Overlay `docker-compose.perf.yml` + `make perf-up` sets `hub-api` `uvicorn --workers 4`, `OTEL_SDK_DISABLED=true` on the API, and Compose `--scale hub-outbound-worker=2 --scale hub-outbox-relay=2` (Locust mix is outbound ingest; delivery drain needs competing consumers). Kafka prefetch / `max.poll.records` stay at code defaults — do not inflate to fake RPS. Accept clock = Locust HTTP 202. Delivery clock = Postgres (`deliveries.status` / unpublished `outbox_events`). Extra replicas will **not** appear as Prometheus targets (baked scrape config); use SQL + `docker stats`.

**Tech stack:** existing Locust harness, optional k6 `ramping-arrival-rate` (additive, no `--no-thresholds`), Compose scale, uvicorn workers.

## Global constraints

- SoT: `spec.md` v3.1 EN + ADR 001–010 + `AGENTS.md`.
- **Do not commit. Not Stage Done.** No port shifts. No `down -v`. No prune.
- Locust mix remains outbound accept (`POST /internal/v1/outbound/events` → 202). Inbound argon2 HMAC is **not** this mix — do not “fix” argon2 unless Clock A proves inbound is on the path.
- Outbound auth is `ADMIN_BOOTSTRAP_TOKEN` equality (not argon2). Rate-limit token-bucket is **inbound**. Do **not** set `REDIS_URL=""` on workers (circuit breaker). Optional empty Redis on **api only** if settings accept it without crash; if API would fail to boot, skip that knob and document.
- Engine today: `create_async_engine(..., pool_pre_ping=True)` — SQLAlchemy defaults (pool 5 + overflow 10). Overlay **must not** raise Postgres `max_connections`. If `TooManyConnections` appears, limiter = pool budget (Wave 5), not `max_connections++`.
- `LOAD_WAIT_MIN`/`LOAD_WAIT_MAX` already exist; hunt uses `0`/`0`. Smoke defaults 0.1/0.5 **unchanged**.
- `LOAD_LOCUST_OTEL` unset during hunt.
- Scheduled k6 arrival ≠ achieved RPS. `http_req_failed` `rate<0.05` = **5%**. Early `dropped_iterations` = too few VUs; later drops + p50 climb = SUT.
- Laptop RPS is **not** spec §8.1 (100/500/2000 req/s). Footnote facts only.
- Frozen ports. Project `b2b-partner-integration-hub`.
- English docs. No `Task N` in `app/`/Compose/scripts.
- Implementer ≠ Reviewer. Task 2 is **read-only** on `app/`.
- `make stack-down` must include `-f docker-compose.perf.yml` so overlay profiles do not stay Up.

## Overlay topology (laptop, spec-aligned)

| Service | Overlay | Why |
|---------|---------|-----|
| `hub-api` | uvicorn `--workers 4` | Prod-like ASGI; default is 1 process |
| `hub-outbox-relay` | `--scale 2` | Competing SKIP LOCKED publishers to Kafka |
| `hub-outbound-worker` | `--scale 2` | Competing Kafka consumers → partner-mock |
| Celery / UI / mock | 1 | Not in Locust mix |
| Kafka | 1 broker (Compose) | Spec allows RF=1 locally; do not add brokers to chase RPS |
| Prefetch | unchanged | Do not raise `max.poll.records` |

## Git vs gitignore

Tracked: `docker-compose.perf.yml`, Makefile, pin tests, `docs/runbooks/load-testing.md`, `docs/perf/ceiling-prodlike.md`, spec footnote if facts exist. Ignored: `.local/`, `.env`, `.superpowers/`.

---

### Task 1: overlay + Make + pin tests (no live hunt)

**Files:**
- Create: `docker-compose.perf.yml`
- Create: `tests/unit/test_perf_overlay.py`
- Modify: `Makefile` (`perf-up`, `stack-down` includes overlay file)
- Modify: `docs/runbooks/load-testing.md` (knob table; overlay is **not** default `stack-up`)

- [x] **Step 1: Failing tests** `tests/unit/test_perf_overlay.py` (read files as text):

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_perf_compose_sets_uvicorn_workers() -> None:
    text = (ROOT / "docker-compose.perf.yml").read_text(encoding="utf-8")
    assert "hub-api" in text
    assert "--workers" in text
    assert "4" in text
    assert "OTEL_SDK_DISABLED" in text


def test_makefile_perf_up_scales_consumers() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "perf-up:" in makefile
    block = makefile.split("perf-up:")[1].split("\n\n")[0]
    assert "docker-compose.perf.yml" in block
    assert "hub-outbound-worker=2" in block
    assert "hub-outbox-relay=2" in block
    assert "LOAD_LOCUST_OTEL=1" not in block


def test_stack_down_includes_perf_overlay() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    down = makefile.split("stack-down:")[1].split("\n\n")[0]
    assert "docker-compose.perf.yml" in down
    assert " -v" not in down.replace("--remove-orphans", "")
```

- [x] **Step 2: RED** pytest that file.

- [x] **Step 3: `docker-compose.perf.yml`** — override `hub-api` command:

```yaml
# Characterization overlay only. Not default stack-up. Not spec §8.1 proof.
services:
  hub-api:
    command:
      [
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--workers",
        "4",
      ]
    environment:
      OTEL_SDK_DISABLED: "true"
```

Match the **actual** uvicorn module path from `docker-compose.yml` (`app.main:app` vs factory). Do not change ports.

- [x] **Step 4: Makefile**

```make
perf-up:
	$(COMPOSE) -f docker-compose.yml -f docker-compose.perf.yml up -d --build --wait \
		--scale hub-outbound-worker=2 --scale hub-outbox-relay=2

stack-down:
	$(COMPOSE) -f docker-compose.yml -f docker-compose.perf.yml down --remove-orphans
```

Keep `stack-up` **without** the overlay. `perf-up` is the hunt entry. Help text: overlay not NFR proof.

- [x] **Step 5: Runbook** table of knobs (workers, scale, wait=0, OTEL off, prefetch unchanged).

- [x] **Step 6: GREEN** pin tests + `make help` shows perf-up. **Do not** start Compose in this task. Do not commit.

**Acceptance:** default stack-up 1×; overlay file tracked; stack-down lists perf file; pin tests PASS.

---

### Task 2: live ceiling hunt (read-only `app/`)

**Files:**
- Create: `docs/perf/ceiling-prodlike.md`
- Modify: `spec.md` **only** a footnote of measured laptop facts if you have them (do not change §8.1 table numbers)
- Modify: `docs/runbooks/load-testing.md` (how to hunt)
- Modify: `AGENTS.md` §8/`make perf-up`

**Forbidden:** changes under `app/`, workers, pool sizes, prefetch, `--scale` as a “fix”, `max_connections`.

**Procedure:**

1. If needed, `docker compose -p <other-numbered-project> down` only. Then **fail-closed** optional. Then:

```bash
make perf-up
make seed
# inspect: hub-api Cmd contains --workers 4; 2 workers + 2 relays
docker compose -p b2b-partner-integration-hub ps
set -a && source .env && set +a
LOAD_WAIT_MIN=0 LOAD_WAIT_MAX=0 LOAD_USERS=50 LOAD_SPAWN_RATE=25 LOAD_RUN_TIME=60s make load-locust
```

2. **Clock A (accept):** Locust CSV `# reqs`, RPS, fail%, p50/p99. If RPS ≈ users / mean_RT, still client-shaped — double users until stop (fail%>1, p50×2, CPU peg, 5xx). Record command + overlay.

3. **Clock B (delivery):** SQL mid-run and after: count unpublished `outbox_events`; count `deliveries` by status; `pg_stat_activity` vs `SHOW max_connections`; Kafka group lag if cheap (`kafbat-ui` or `kafka-consumer-groups`); `docker stats` snapshot (api vs postgres vs kafka vs relay vs worker).

4. Classify **one** primary limiter using:

| Observation | Verdict |
|-------------|---------|
| HTTP 500 TooManyConnections / activity at cap | pool budget vs max_connections |
| Unpublished outbox grows, Kafka queues thin, workers ~0% CPU | **relay/publish**, not workers |
| Unpublished=0, Kafka lag grows, worker CPU | workers in play (only if lag holds on long hold) |
| wait 0.1–0.5 and RPS≈users/wait | still client; wait=0 required |
| API CPU pegged, DB idle, unpublished=0 | API/process (uvicorn already 4) |
| p50 grows, fail% low, docker stats postgres CPU | DB |

5. Optional additive: short k6 `ramping-arrival-rate` via existing Grafana runner **or** document skip. No `--no-thresholds`. Record scheduled vs achieved.

6. `docs/perf/ceiling-prodlike.md`: overlay, commands, Clock A table, Clock B SQL/stats, **named limiter**, what was **not** proven (§8.1).

7. `make stack-down` (overlay included). Confirm empty ps. No `-v`.

**Acceptance:** one named limiter with evidence; no `app/` diff; stack down; no invented RPS as NFR.

---

## Out of Wave 4

Wave 5: TDD fix of the **named** limiter only. Wave 6: rebuild + remesure same overlay.
