# Partner Integration Hub — Stage 2 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Implementer ≠ Reviewer. Local: no push. **Do not git commit.** Checklists `- [ ]`.
> Execute per [`AGENTS.md` §10.3](../../AGENTS.md). Role prompts: [`docs/agentic/role-prompts/`](../agentic/role-prompts/).

**Goal:** Industrial hub: transactional outbox + `hub-outbox-relay`, Redis circuit breaker, retry tiers `30s`/`1m`/`5m`/`15m`/`1h` with delay honoring `scheduled_at`, token-bucket rate limits, signing-secret rotation table, bulk replay, DLQ ack/purge, Celery beat (no webhook POST), analytics APIs, compliance Grafana, AsyncAPI CI.

**Architecture:** Same single package `app/`. After this stage the API **must not** publish to Kafka in the request path: persist `deliveries`/`inbound_events` **and** `outbox_events` in one PostgreSQL transaction; `hub-outbox-relay` publishes then sets `published_at`. Stage 1 publish-after-commit path is **removed** (ADR-007). Celery is Redis-broker scheduled maintenance only (ADR-002).

**Tech Stack:** Existing Stage 1 stack + Celery 5.x (Redis broker/backend), `opentelemetry-instrumentation-fastapi` + `opentelemetry-instrumentation-httpx`. Kafka client remains **aiokafka**.

## Global Constraints

- Dual-id only on `partners` and `deliveries`; sequential BIGINT never in DTO/OpenAPI/UI/Kafka/replay (ADR-009). Outbox PK is BIGINT and **never** appears in HTTP.
- At-least-once; no exactly-once claims (ADR-001).
- Kafka retries, not Celery transport (ADR-002). Celery must not POST partner webhooks.
- Kafka client **aiokafka**; message key = partner `public_id` (ADR-003).
- HMAC-SHA256 on raw body + `timestamp.`; skew >300s → 403; `hmac.compare_digest`; Stage 2 tries `previous` inside overlap; `revoked` rejected (ADR-004).
- After Stage 2: transactional outbox + relay only — no silent dual-write, no leftover API Kafka publish for persist paths (ADR-007).
- Circuit breaker per partner in Redis; Redis down → **fail-open** outbound (ADR-005). Gauge `hub_circuit_breaker_state` uses `partner_slug` + `state`, never UUIDs.
- SLA clock stops at `first_success_at` (ADR-008). Replay does not mutate payload or idempotency key.
- Thin UI: no HMAC/retry/outbox/CB in the browser (ADR-006).
- OTel OTLP to Collector; traces → **Jaeger**; metrics → Prometheus. Not Tempo. Metric attributes: `partner_slug` not UUIDs.
- Correlation: UUIDv7; generate if missing; invalid → 422.
- Pagination: `limit` + `offset` only (default 50, max 200).
- Poison: 400/401/403/404/422 → failed+DLQ immediately; 408/429/5xx/network → retry.
- OpenAPI live `/docs` bar unchanged (title, summary, description, tags, Field descriptions, examples, documented errors the handler raises).
- Frozen ports unchanged. Appendix A env names exact. Empty `OTEL_SDK_DISABLED=` must not crash.
- No git commit/push. No OFOM/billing/SSO invariant copy-paste.
- Imports at module top. No `Task N` in application code after the task ships.
- Retry topic mapping (spec §6.6 + §3.4): attempt 2 → `hub.outbound.retry.30s`; attempts 3–4 → `5m`; 5–6 → `15m`; 7–8 → `1h`. Also create `hub.outbound.retry.1m` and select it when computed delay is in `(45s, 90s]` (spec §3.4). Consumer **must not** HTTP-POST a retry message before `scheduled_at`.
- Bulk replay respects open circuits. Rate-limit bulk-replay.

---

## File map (Stage 2)

| Path | Responsibility |
|------|----------------|
| `app/domain/services/retry_topics.py` | Delay → Kafka retry topic |
| `app/domain/services/circuit_breaker.py` | Redis CB closed/open/half-open |
| `app/domain/services/rate_limit.py` | Token bucket (replace INCR window) |
| `app/domain/services/outbox.py` | Insert unpublished `outbox_events` in-session |
| `app/workers/outbox_relay.py` | Publish unpublished rows; set `published_at` |
| `app/domain/models/signing_secret.py` | `partner_signing_secrets` |
| `app/celery_app/` | Beat + tasks: stale replay, idempotency purge, rotation notify |
| `app/api/v1/admin/analytics.py` | Partner summary + overview |
| `app/api/v1/admin/dead_letters.py` | ack/purge |
| `app/api/v1/admin/deliveries.py` | bulk-replay |
| `admin_ui/` | Filters, bulk replay, DLQ ack, partner compliance |
| `infra/kafka/create-topics.sh` | New retry + audit topics |
| `docs/asyncapi/asyncapi.yaml` | New channels |
| `.github/workflows/ci.yml` | `asyncapi validate` job |

---

## Spec §3.4 Must → tasks

| Must | Tasks |
|------|-------|
| HTTP traces actually in Jaeger | 0 |
| Retry tiers + jitter mapping + honor `scheduled_at` | 1 |
| Transactional outbox write path | 2 |
| `hub-outbox-relay` catch-up | 3 |
| Redis circuit breaker | 4 |
| Token-bucket rate limits | 5 |
| Signing secret rotation table | 6 |
| Bulk replay + DLQ ack/purge | 7 |
| Analytics / partner summary | 8 |
| Celery beat maintenance | 9 |
| Admin UI filters/bulk/compliance | 10 |
| Grafana compliance + alerts | 11 |
| AsyncAPI CI + unskip §10.3 Stage 2 tests + evidence | 12 |

---

### Task 0: FastAPI + httpx OTel instrumentation

**Files:**
- Modify: `pyproject.toml` (add `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-asgi` within current OTel major), `uv.lock`, `app/observability/otel.py`, `app/main.py` (`create_app` instruments FastAPI), `app/integrations/http_client.py` (use instrumented client or `HTTPXClientInstrumentor().instrument()`), `Dockerfile` if lock changes
- Test: `tests/unit/test_otel_instrumentation.py`

**Interfaces:**
- Consumes: `configure_otel(service_name, settings)`
- Produces: Incoming HTTP spans on `hub-api`; outgoing httpx spans from outbound worker when SDK enabled

**Spec:** prompt §2.4 traces from Stage 1; Jaeger native OTLP

**Acceptance:**
- When `OTEL_SDK_DISABLED` is false, `create_app()` is wrapped with FastAPI instrumentor **after** routes are included
- When SDK disabled, no instrumentor crash
- Unit test: with SDK disabled, `create_app()` still serves `/inbound/v1/health`
- Official docs: https://opentelemetry.io/docs/languages/python/instrumentation/

**Steps:**

- [ ] **Step 1: Failing test** that imports instrumentor helpers and asserts `instrument_fastapi(app, settings)` is a no-op when `otel_sdk_disabled=True`

```python
# tests/unit/test_otel_instrumentation.py
from app.config import Settings
from app.main import create_app
from app.observability.otel import instrument_fastapi


def test_instrument_fastapi_noop_when_sdk_disabled() -> None:
    settings = Settings(otel_sdk_disabled=True)
    app = create_app()
    instrument_fastapi(app, settings)  # must not raise
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_otel_instrumentation.py -v` → FAIL (missing `instrument_fastapi`)
- [ ] **Step 3: Implement** `instrument_fastapi` / `instrument_httpx` in `app/observability/otel.py`; call from `create_app()` and outbound consumer startup
- [ ] **Step 4: Run test** → PASS; `make ci` still green
- [ ] **Step 5: Do not commit**

---

### Task 1: Retry tiers + honor `scheduled_at`

**Files:**
- Create: `app/domain/services/retry_topics.py`, `tests/unit/test_retry_topics.py`
- Modify: `infra/kafka/create-topics.sh`, `app/integrations/kafka_producer.py` (generic `publish_outbound_retry(topic=...)`), `app/workers/outbound_processor.py` (select topic from delay/attempt), `app/workers/outbound_consumer.py` (`CONSUME_TOPICS` includes all retry tiers; wait until `scheduled_at` before `process_outbound_message` for retry topics), `docs/asyncapi/asyncapi.yaml`
- Test: `tests/unit/test_retry_topics.py`, extend `tests/unit/test_outbound_processor.py` for topic selection

**Interfaces:**
- Consumes: `compute_delay_seconds(attempt_number, ...)`
- Produces:

```python
def retry_topic_for(attempt_number: int, delay_seconds: float) -> str: ...
```

Mapping (must match tests):

| Condition | Topic |
|-----------|-------|
| `attempt_number <= 2` or `delay_seconds <= 45` | `hub.outbound.retry.30s` |
| `delay_seconds <= 90` | `hub.outbound.retry.1m` |
| `attempt_number <= 4` or `delay_seconds <= 450` | `hub.outbound.retry.5m` |
| `attempt_number <= 6` or `delay_seconds <= 1350` | `hub.outbound.retry.15m` |
| else | `hub.outbound.retry.1h` |

Prefer the **finest** topic that satisfies spec §6.6 attempt table when both apply (attempt 3 → `5m` even if delay is 120s).

- Consumer: if `message.topic != hub.outbound.pending` and envelope `scheduled_at` is in the future, `asyncio.sleep` remaining seconds (cap per-sleep 5s loop so shutdown stays responsive) **without committing** until processed.

**Acceptance:**
- Topics created idempotently: `hub.outbound.retry.1m`, `.5m`, `.15m`, `.1h` (keep `.30s`)
- Processor no longer always publishes `retry.30s`
- Unit tests for mapping table + “do not process retry before scheduled_at” (inject clock)
- `make ci` green

**Steps:**

- [ ] **Step 1: Failing tests** for mapping table (attempt 2/4/6/8 and delay 30/60/120/900/3600)
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement mapping + producer/consumer/processor wiring**
- [ ] **Step 4: PASS + `make ci`**
- [ ] **Step 5: Do not commit**

---

### Task 2: Transactional outbox write path (kill API Kafka publish)

**Files:**
- Create: `app/domain/services/outbox.py`, `tests/unit/test_outbox_insert.py`
- Modify: `app/api/v1/internal/outbound.py` (insert outbox instead of `publish_outbound_pending`), `app/api/v1/inbound/events.py` (insert outbox instead of `publish_inbound_event`), `app/domain/services/replay_service.py` (outbox row for pending replay), `app/workers/outbound_processor.py` (retry/DLQ/SLA events also via outbox **or** worker may still produce directly — **decision:** worker HTTP outcomes may produce Kafka directly to avoid relay lag on retries; **persist path** (API inbound/outbound/replay) **must** use outbox. Document in ADR-007 amendment paragraph if worker still produces retry/DLQ.)
- Locked decision: **API persist paths = outbox only.** Worker-originated retry/DLQ/SLA may keep direct Kafka produce (already after DB commit of attempt row). Do not dual-write persist path.

**Interfaces:**

```python
def enqueue_outbox(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: int,
    topic: str,
    payload: dict[str, object],
    key: str,
) -> OutboxEvent: ...
```

`payload` is the Kafka JSON envelope (UUID public ids). `key` is partner `public_id` string stored inside payload or a `message_key` field — add `message_key VARCHAR(64)` column if not present (Alembic revision). If adding a column, new revision `20260601_0003_outbox_message_key.py`.

**Acceptance:**
- Creating outbound delivery: one TX with delivery + outbox; **no** `producer.send` in the request handler
- Duplicate inbound: no second outbox row
- Kafka down during HTTP request: still **202** for outbound (row+outbox committed); inbound same as today for persist success
- Unit tests with session + `RecordingKafkaProducer` unused by handler
- `make ci`

**Steps:** TDD insert helper → switch inbound/outbound/replay handlers → assert handlers do not call `publish_*`.

---

### Task 3: `hub-outbox-relay` + Compose + catch-up test

**Files:**
- Create: `app/workers/outbox_relay.py`, `tests/unit/test_outbox_relay.py`, `tests/integration/test_outbox_catch_up.py` (unskip/replace `test_outbox_catch_up_stage2`)
- Modify: `Dockerfile` CMD remains API default; Compose new service `hub-outbox-relay` same image, `command: ["python", "-m", "app.workers.outbox_relay"]`, `healthcheck.disable: true`, `KAFKA_BOOTSTRAP_SERVERS=kafka:19092`, `read_only` + tmpfs, `stop_grace_period: 30s`
- Metric: `hub_outbox_unpublished` gauge (count of `published_at IS NULL`); `partner_slug` only if available from payload, else omit slug rather than use UUID

**Interfaces:**

```python
async def publish_unpublished_batch(
    session: AsyncSession,
    producer: AIOKafkaProducer,
    *,
    limit: int = 100,
) -> int: ...
```

Select `WHERE published_at IS NULL ORDER BY created_at FOR UPDATE SKIP LOCKED`. Publish `send_and_wait`. Set `published_at`, increment `publish_attempts` on failure (leave unpublished).

**Acceptance:**
- Integration: insert unpublished row while producer failing / Kafka unreachable in-process → row unpublished → relay with working producer → Kafka received + `published_at` set
- Compose service exists; `make compose-up` may stay data-plane-only; full `docker compose up` includes relay
- ADR-007: Stage 1 discrepancy path gone from persist handlers
- `make ci` + targeted integration

**Prove (prompt §15.3):** Kafka down after delivery insert → unpublished row → relay catch-up.

---

### Task 4: Redis circuit breaker

**Files:**
- Create: `app/domain/services/circuit_breaker.py`, `tests/unit/test_circuit_breaker.py`
- Modify: `app/workers/outbound_processor.py` (before HTTP: if open, skip POST, schedule retry, do not count as poison), `app/observability/metrics.py` (already has `hub_circuit_breaker_state`), replay/bulk (Task 7 will call `is_open`)
- Redis keys: `cb:{partner_slug}:failures`, `cb:{partner_slug}:state` — **slug not UUID**
- Defaults: Appendix A `HUB_CIRCUIT_FAILURE_THRESHOLD=10`, `WINDOW=60`, `OPEN=300`
- Redis down: return closed (fail-open); log warning

**States:** `closed` → `open` after N failures in window → after `open_duration` → `half_open` (single probe) → success `closed` / failure `open`.

**Acceptance:**
- Unit tests for all transitions with fake Redis
- Processor: open circuit → no httpx POST
- Unskip `tests/integration/test_fault_injection.py::test_circuit_open_pauses_deliveries_stage2` or replace with real test
- `make ci`

---

### Task 5: Token-bucket rate limits

**Files:**
- Modify: `app/domain/services/rate_limit.py` (true token bucket, not INCR-per-second), inbound handler, bulk-replay (Task 7), optional outbound worker throttle
- Test: `tests/unit/test_rate_limit.py` (extend)

**Algorithm:** Redis `INCRBYFLOAT` / Lua or: tokens key + timestamp key; refill `(now-last)*rps` capped at burst=`rate_limit_rps`. Fail-open on Redis error.

**Acceptance:**
- Burst of `rps+1` in 1ms rejects one request when Redis up
- Redis error → allow
- Metric `hub_rate_limit_rejected_total{partner_slug=...}`
- `make ci`

---

### Task 6: `partner_signing_secrets` rotation

**Files:**
- Create: `app/domain/models/signing_secret.py`, `alembic/versions/20260601_0004_partner_signing_secrets.py`, `app/domain/services/signing_secrets.py`, `tests/unit/test_signing_secret_rotation.py`
- Modify: inbound HMAC verify (primary then previous if `valid_until` in future; revoked never), outbound sign (primary only), `app/api/v1/admin/partners.py` `POST /admin/v1/partners/{id}/signing-secrets/rotate` (`RequireAdmin`), audit `signing_secret.rotate`, seed to insert primary row (keep `partners.signing_secret_encrypted` populated for backfill then copy into table)
- Overlap: `HUB_SECRET_ROTATION_OVERLAP_HOURS=24`

**Acceptance:**
- Previous HMAC accepted inside window; revoked rejected (403)
- Rotate audited; plaintext new secret shown once
- Dual-id: table PK is UUIDv7 (spec §6.3 satellite) — **not** BIGINT
- `make ci` + `make migrate` on local PG

---

### Task 7: Bulk replay + DLQ ack/purge

**Files:**
- Modify: `app/api/v1/admin/deliveries.py` `POST /admin/v1/deliveries/bulk-replay` body `{delivery_ids: UUID[], reason: str}` `RequireAdmin`, rate-limited; `app/api/v1/admin/dead_letters.py` `POST .../{id}/ack` `RequireOperator`, `POST .../{id}/purge` `RequireAdmin` with reason; audit `delivery.replay` / `dlq.ack` / `dlq.purge`; OpenAPI responses 401/403/404/409/422
- Create: `tests/unit/test_bulk_replay.py`, `tests/unit/test_dlq_ack_purge.py`
- Bulk: skip/open-circuit partners (count in response); empty reason → 422; max 100 ids

**Acceptance:**
- Viewer bulk/ack/purge → 403
- Ack sets `acknowledged_at`/`acknowledged_by`; purge audited and does not delete delivery history (mark reason `manual_purge` or status — do **not** silently DELETE deliveries)
- `make export-openapi`; OpenAPI unit still green

---

### Task 8: Analytics APIs

**Files:**
- Create: `app/api/v1/admin/analytics.py`, `app/schemas/analytics.py`, `tests/unit/test_analytics_api.py`
- Modify: `app/main.py` include router
- Routes (spec §7.1.4): `GET /admin/v1/analytics/partners/{id}/summary`, `GET /admin/v1/analytics/overview`
- JSON `id` = partner public UUID. Metrics: success_rate, sla_compliance_pct, sla_breaches, circuit_state (`unknown` if Redis down), dlq_age_seconds. No BIGINT.

**Acceptance:** Field descriptions; `RequireViewer`; `make ci`

---

### Task 9: Celery beat (no webhook POST)

**Files:**
- Create: `app/celery_app/__init__.py`, `app/celery_app/celery.py`, `app/celery_app/tasks.py`, `tests/unit/test_celery_tasks.py`
- Modify: `pyproject.toml` (`celery[redis]`), Compose `hub-celery-beat` + `hub-celery-worker`, `.env.example` `CELERY_BROKER_URL=redis://localhost:6379/1`
- Tasks: `replay_stale_failed` (only `auto_replay_enabled` and circuit closed; audit `trigger=scheduled`), `purge_old_idempotency_keys` (Redis TTL already — task documents/no-op or deletes expired inbound cache keys), `rotate_webhook_secrets` **notify only** (log + metric, do not rotate unless admin API). **Forbidden:** httpx POST to partner URL from Celery.

**Acceptance:**
- Unit tests assert tasks never import `post_outbound`
- Beat schedule in code (not undocumented crontab only)
- `make ci`

---

### Task 10: Thin admin UI — filters, bulk, DLQ ack, compliance

**Files:**
- Modify: `admin_ui/src/api/client.ts`, `DeliveriesList.tsx` (status/partner/event_type filters already partly in API — wire query params + bulk replay with reason), `DeadLetters.tsx` (ack button), `PartnersList.tsx` or new `PartnerCompliance.tsx` calling analytics summary
- Exhaustive `switch` + `never` for any new unions
- No HMAC/retry/outbox/CB logic in the browser

**Acceptance:** `npm`/Docker UI build; replay/bulk disabled until trimmed reason; talks only to `/admin/v1`

---

### Task 11: Grafana SLA compliance + alerts

**Files:**
- Modify: `docs/grafana/dashboards/sla_compliance.json` (success rate, SLA breaches, circuit open, DLQ age — `by (partner_slug)`), `infra/prometheus/alerts.yml` (circuit open, compliance drop, unacked DLQ age, keep DLQ growth)
- Modify: `docs/slo.md` interpretation; `docs/runbooks/dlq-response.md` if new alerts
- Idle `rate()` NaN note stays

**Acceptance:** No UUID labels in PromQL. Alert names + runbook pointers.

---

### Task 12: AsyncAPI CI + fault-injection unskip + Stage 2 evidence

**Files:**
- Modify: `docs/asyncapi/asyncapi.yaml` (retry tiers, `hub.audit.events` optional, `hub.outbound.delivered` optional), `.github/workflows/ci.yml` (`asyncapi validate` via official CLI or `@asyncapi/cli` npx in CI), `Makefile` `test-contract` may invoke validate if CLI present
- Unskip integration tests for circuit, burst replay, outbox catch-up
- Create: local gitignored Stage 2 DoD evidence template; orchestrator fills after live prove
- Do **not** write “Stage 2 Done”

**Acceptance:**
- CI job validates AsyncAPI
- `make ci` green
- Evidence checklist: outbox catch-up, CB metric, bulk replay audit, previous HMAC accepted, Celery `trigger=scheduled`

---

## Self-review (orchestrator)

- Spec §3.4 rows map to tasks 0–12.
- No TBD / “similar to Task N” / git commit steps.
- Stage 3 (multi-URL routing, schema registry, replay approval, partner status API, k6, Kafka `traceparent`, HA Kafka docs) is **out of this plan**.
- Worker may still produce retry/DLQ Kafka after attempt commit; API persist path must not.
