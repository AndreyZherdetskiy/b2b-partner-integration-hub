# Wave 7 — accept-path software (no workers, no CPU quota)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut **process CPU and event-loop cost** on the outbound accept path (named Wave 6 limiter: API/process at overlay `cpus: "4.0"`) using software only, then remesure the **same** overlay.

**Architecture:** Wave 6 still pegged four uvicorn workers at the 4.0 quota; in-process health (no DB) p50 rose 66→160 ms when users doubled. Two software levers: (1) replace Starlette `BaseHTTPMiddleware` (task-group per request) with **pure ASGI** for correlation + max-body — this runs on every request including health; (2) **process-local TTL cache** of partner / schema / endpoints on enqueue so the hot path keeps one idempotency SELECT + INSERT/COMMIT, not four sequential lookups. Do **not** add workers, `--scale`, overlay CPU, or `max_connections`.

**Tech stack:** Starlette pure ASGI middleware, in-process dict TTL cache, existing pytest, Locust remesure.

## Global constraints

- SoT: `spec.md` v3.1 EN + ADR 001–010 + `AGENTS.md`.
- **Do not commit. Not Stage Done.** No port shifts. No `down -v`. No prune.
- **No new workers. No overlay CPU bump. No `--scale`. No `max_connections++`. No prefetch++.**
- Locust mix unchanged. `LOAD_WAIT_MIN/MAX=0` on remesure. `LOAD_LOCUST_OTEL` unset. Do not empty `REDIS_URL`.
- Cache must be **invalidated** on admin partner/endpoint/schema writes (status, event_types, SLA, new schema). Do not cache Bearer tokens or raw secrets.
- Detached ORM rows used only for already-loaded scalars (`id`, `public_id`, `status`, `sla_seconds`, `max_attempts`, `json_schema`). No lazy loads.
- English docs. No `Task N` in `app/` / Compose / scripts.
- Implementer ≠ Reviewer. Task 1–2 TDD. Task 3 live remesure is read-only on `app/` except docs.
- Official: [Starlette pure ASGI middleware](https://www.starlette.io/middleware/#pure-asgi-middleware). `BaseHTTPMiddleware` is known to be expensive (extra task per request).

## Git vs gitignore

Tracked: middleware, cache module, enqueue wiring, invalidation, pin tests, remesure doc, spec footnote facts. Ignored: `.env`, `.venv/`, `.local/`, `.superpowers/`.

---

### Task 1: pure ASGI correlation + max-body middleware

**Files:**
- Modify: `app/api/middleware/correlation.py`
- Modify: `app/api/middleware/max_body.py`
- Modify: `app/main.py` — `inbound_health` become `async def` (no threadpool hop)
- Existing tests: `tests/unit/test_correlation.py` must stay GREEN (header echo, UUIDv4→422, body 413)

**Forbidden:** Compose, overlay, cache, git commit.

**Design:** Implement `CorrelationMiddleware` and `MaxBodySizeMiddleware` as classes with `async def __call__(self, scope, receive, send)` (or Starlette `Middleware` wrapping a function). Preserve:

- Missing `X-Correlation-Id` → generate UUIDv7, echo on response
- Invalid / v4 UUID → 422 JSON (same body as today), do not echo the bad value
- `Content-Length` over `hub_max_payload_bytes` → 413 (same body as today)
- Do not buffer the full body when `Content-Length` is absent (keep current behaviour: length-header only)

Starlette `BaseHTTPMiddleware` must **not** remain on these two classes.

- [ ] **Step 1: Confirm RED path** — temporarily rename `dispatch` or assert in a new pin that `CorrelationMiddleware` is not a `BaseHTTPMiddleware` subclass:

```python
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.middleware.correlation import CorrelationMiddleware
from app.api.middleware.max_body import MaxBodySizeMiddleware


def test_correlation_middleware_is_pure_asgi() -> None:
    assert not issubclass(CorrelationMiddleware, BaseHTTPMiddleware)


def test_max_body_middleware_is_pure_asgi() -> None:
    assert not issubclass(MaxBodySizeMiddleware, BaseHTTPMiddleware)
```

Add these to `tests/unit/test_correlation.py`. Run: expect FAIL while they still subclass `BaseHTTPMiddleware`.

- [ ] **Step 2: Implement pure ASGI** for both middleware. Keep helper `resolve_correlation_id`. `inbound_health` → `async def`.

- [ ] **Step 3: GREEN** `uv run pytest tests/unit/test_correlation.py -q` (existing + new pins). Do not commit.

**Acceptance:** not `BaseHTTPMiddleware`; existing correlation/413 tests PASS; health is async.

---

### Task 2: process-local accept-path cache

**Files:**
- Create: `app/domain/services/accept_path_cache.py`
- Create: `tests/unit/test_accept_path_cache.py`
- Modify: `app/domain/services/outbound_enqueue.py` — use cache around partner / schema / endpoints fetches; still SELECT existing deliveries every time
- Modify: `app/api/v1/admin/partners.py` — after successful PATCH partner (and POST create if it can change lookups), `invalidate_partner(public_id)`
- Modify: `app/api/v1/admin/endpoints.py` — after POST/PATCH endpoint, `invalidate_partner_endpoints(partner_id)` (internal BIGINT id)
- Modify: `app/api/v1/admin/schemas.py` — after POST schema, `invalidate_schema(event_type)`
- Modify: `docs/perf/ceiling-prodlike.md` or remesure doc later — Task 3 owns remesure prose; here a one-line “Wave 7 intended: L1 accept cache” is enough if you touch docs

**Forbidden:** overlay CPU, `--scale`, live Locust, git commit.

**Cache API (exact names):**

```python
# app/domain/services/accept_path_cache.py
from __future__ import annotations

import time
from typing import TypeVar
from uuid import UUID

from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.models.payload_schema import PayloadSchema

T = TypeVar("T")
DEFAULT_TTL_SECONDS = 30.0

def cache_get(key: str) -> object | None: ...
def cache_set(key: str, value: object, *, ttl: float = DEFAULT_TTL_SECONDS) -> None: ...
def invalidate_partner(public_id: UUID) -> None: ...
def invalidate_partner_endpoints(partner_id: int) -> None: ...
def invalidate_schema(event_type: str) -> None: ...
def reset_accept_path_cache() -> None: ...  # tests
```

Keys: `partner:{uuid}`, `endpoints:{partner_id}:{event_type}`, `schema:{event_type}`.

Enqueue: on miss, existing fetch functions + `cache_set`. On hit, skip those SELECTs. **Always** call `fetch_deliveries_by_source_event_id` (unique idempotency_key per Locust request).

TTL expiry: store `(expires_at_monotonic, value)`; treat expired as miss.

- [ ] **Step 1: Failing tests** `tests/unit/test_accept_path_cache.py` — set/get, expiry (monkeypatch `time.monotonic`), `invalidate_partner` drops partner key, `reset_accept_path_cache`. Plus enqueue test: FakeSession execute-count for partner/schema/endpoints drops on second `enqueue_outbound_for_event` with the same partner/event_type and **different** idempotency_key (second call still queries deliveries). Reuse FakeSession style from `tests/unit/test_delivery_create.py` / `test_schema_registry.py`.

- [ ] **Step 2: RED** pytest that file.

- [ ] **Step 3: Implement cache + wire enqueue + invalidate on admin writes (after commit).**

- [ ] **Step 4: GREEN** new tests + a slice of existing outbound unit tests (`tests/unit/test_delivery_create.py`, `tests/unit/test_endpoint_fanout.py`, `tests/unit/test_schema_registry.py`). Call `reset_accept_path_cache()` in new tests’ setup/teardown so order is isolated.

**Acceptance:** second enqueue does not re-SELECT partner/schema/endpoints; idempotency SELECT still runs; admin PATCH invalidates; no overlay changes.

---

### Task 3: remesure same overlay (read-only `app/`)

**Files:**
- Create: `docs/perf/ceiling-accept-path.md`
- Modify: `spec.md` §8.1 **footnote facts only** (do not change the 100/500/2000 table in this task)
- Modify: `docs/runbooks/load-testing.md` link
- Modify: `AGENTS.md` §10.5 one line

**Forbidden:** `app/` edits, extra workers, CPU quota change, `max_connections`, git commit.

**Procedure:**

1. `make perf-up` (must `--build`) then `make seed`. Same overlay as Wave 6 (`cpus: "4.0"`, workers 4, scale 2/2). Inspect NanoCpus still 4.0.
2. `set -a && source .env && set +a`. Unset `LOAD_LOCUST_OTEL`.
3. Locust wait=0, 50 users / 60s, then 100 users / 60s. Compare Clock A to Wave 6 (`docs/perf/ceiling-remeasure.md`: 50u outbound ~234 RPS p50 170 ms; 100u ~219 RPS p50 380 ms).
4. Clock B: unpublished, deliveries by status, `pg_stat_activity` vs `max_connections`, `docker stats` (api vs postgres). Name **one** limiter. It may stay API/process or move to DB.
5. Skip k6 ramping. No `--no-thresholds`.
6. Write `docs/perf/ceiling-accept-path.md`. Footnote facts. `make stack-down`. Empty ps. No `-v`.

**Acceptance:** remesure doc; one named limiter; §8.1 table unchanged in this task; stack down.

---

## Out of Wave 7

If Clock A is still API/process after this wave, remaining software is diminishing (Pydantic, JSON body, INSERT). Do **not** add workers or CPU. Orchestrator may then adjust spec numbers **without** a runbook/AGENTS how-to for that adjustment.
