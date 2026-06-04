# Partner Integration Hub — Stage 3 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Implementer ≠ Reviewer. Local: no push. **Do not git commit.** Checklists `- [ ]`.
> Execute per [`AGENTS.md` §10.4](../../AGENTS.md). Role prompts: [`docs/agentic/role-prompts/`](../agentic/role-prompts/).

**Goal:** Enterprise hub: multi-URL `event_type` fan-out, PG JSON Schema registry stub, replay approval, partner status API, W3C `traceparent` on Kafka, weekly compliance export, k6 p95 evidence, HA Kafka / replica **docs**, remaining runbooks, thin UI sandbox + approval.

**Architecture:** Same single package `app/`. Persist path stays transactional outbox (ADR-007). Fan-out creates **one delivery + one outbox row per matching endpoint** in one request transaction. Celery still must not POST partner webhooks.

**Tech Stack:** Existing Stage 2 stack + `jsonschema` (Draft 2020-12) + Grafana k6 image for load. Kafka client remains **aiokafka**. Python **3.12**.

## Global Constraints

- Dual-id only on `partners` and `deliveries`; sequential BIGINT never in DTO/OpenAPI/UI/Kafka/replay (ADR-009). Outbox PK is BIGINT and **never** appears in HTTP.
- At-least-once; no exactly-once claims (ADR-001).
- Kafka retries, not Celery transport (ADR-002).
- Kafka client **aiokafka**; message key = partner `public_id` (ADR-003).
- HMAC-SHA256 on raw body + `timestamp.`; `hmac.compare_digest` (ADR-004).
- Transactional outbox + relay on persist paths (ADR-007).
- Circuit breaker fail-open if Redis down (ADR-005). Metric attributes = `partner_slug`, never UUIDs.
- SLA clock stops at `first_success_at` (ADR-008). Replay does not mutate payload.
- Thin UI: no HMAC/retry/outbox/CB in the browser (ADR-006).
- OTel OTLP HTTP constructor URLs must include `/v1/traces` and `/v1/metrics` (official Python exporter docs). Traces → **Jaeger**, not Tempo.
- Correlation: UUIDv7; invalid → 422.
- Pagination: `limit` + `offset` only (default 50, max 200).
- Frozen ports unchanged. Operator consoles: Kafka UI `:8081`, Redis Commander `:8082`, Adminer `:8083`, Flower `:8084`.
- No git commit/push. No OFOM/billing/SSO invariant copy-paste.
- Imports at module top. No `Task N` in application code after the task ships.
- Compose stays **one Kafka broker, RF=1**. Production RF=3 is documentation only — do not fake three brokers.
- Do not write “Stage 3 Done”.

---

## File map (Stage 3)

| Path | Responsibility |
|------|----------------|
| `app/domain/services/delivery_service.py` | List matching outbound endpoints; derived idempotency keys |
| `app/api/v1/internal/outbound.py` | Fan-out persist + `delivery_ids` in 202/200 |
| `app/domain/models/payload_schema.py` | JSON Schema registry stub (PG) |
| `app/domain/services/schema_registry.py` | Validate payload when a schema exists |
| `app/domain/models/replay_approval.py` | Pending replay requests |
| `app/api/v1/admin/replay_approvals.py` | Request / approve / reject |
| `app/api/v1/partner/deliveries.py` | `GET /partner/v1/deliveries/{id}` |
| `app/observability/trace_context.py` | W3C `traceparent` Kafka headers |
| `app/api/v1/admin/analytics.py` | Weekly compliance export |
| `load/k6/outbound_ingest.js` | k6 POST→expected status |
| `docs/perf/README.md` | Recorded p95, not a fake SLA |
| `docs/architecture.md` | HA Kafka RF=3; optional PG replica |
| `admin_ui/` | Sandbox test + approval queue (Admin API only) |

---

## Spec §3.5 Must → tasks

| Must | Tasks |
|------|-------|
| Several URLs + `event_type` routing (fan-out) | 0 |
| JSON Schema registry stub (PG) | 1 |
| Replay approval (operator request → admin confirm) | 2 |
| `GET /partner/v1/deliveries/{id}` | 3 |
| W3C `traceparent` on Kafka | 4 |
| Weekly SLA compliance export | 5 |
| k6 vs documented p95 | 6 |
| HA Kafka docs + optional replica docs + missing runbooks | 7 |
| UI sandbox + approval queue | 8 |
| Evidence file (no Stage Done) | 9 |

---

### Task 0: Multi-URL event_type fan-out

**Files:**
- Modify: `app/domain/services/delivery_service.py`, `app/api/v1/internal/outbound.py`, `app/schemas/outbound.py`, `docs/adr/010-multi-endpoint-fanout.md` (create Accepted), `tests/unit/test_delivery_create.py`, `tests/unit/test_openapi_docs.py` if response shape changes
- Test: `tests/unit/test_endpoint_fanout.py`

**Locked decision (do not change UNIQUE `(partner_id, idempotency_key)`):**

```python
def derived_idempotency_key(client_key: str, endpoint_public_id: uuid.UUID) -> str:
    return f"{client_key}::{endpoint_public_id}"
```

Store `Delivery.source_event_id = client_key` (caller key). Duplicate: if **any** delivery for this partner already has `source_event_id == client_key`, return **200** with those `delivery_ids` and **do not** create extra rows for newly added endpoints (strict idempotency).

**Interfaces:**

```python
async def fetch_active_outbound_endpoints(
    session: AsyncSession,
    *,
    partner_id: int,
    event_type: str,
) -> list[PartnerEndpoint]: ...
```

Replace `fetch_active_outbound_endpoint` (`.limit(1)`). Zero matches → 422 as today. N matches → N deliveries + N outbox rows in **one** `commit`.

HTTP 202/200 JSON (public UUIDs only):

```json
{ "delivery_id": "<first public_id>", "delivery_ids": ["..."], "status": "accepted" }
```

Keep `delivery_id` as the first id for existing clients. Field descriptions required. OpenAPI examples updated.

**Spec:** §3.5 multi-URL; §7.1.5; ADR-009 unique stays.

**Acceptance:**
- Two active outbound endpoints both listing `order.created` → two deliveries, two outbox rows, same `correlation_id`, different `endpoint_id`
- Endpoint subscribed only to `order.updated` is not selected for `order.created`
- Repeat same `idempotency_key` → 200, no third delivery
- BIGINT never in response
- `make ci` green

**Steps:**

- [ ] **Step 1: Failing tests** in `tests/unit/test_endpoint_fanout.py`:

```python
def test_derived_idempotency_key_includes_endpoint_uuid() -> None:
    endpoint_id = uuid.UUID("0194a2b3-c4d5-7890-abcd-ef1234567890")
    assert derived_idempotency_key("idem-1", endpoint_id) == (
        "idem-1::0194a2b3-c4d5-7890-abcd-ef1234567890"
    )
```

Plus an API test: FakeSession returns two endpoints; POST `/internal/v1/outbound/events` → 202, `len(delivery_ids)==2`, two `OutboxEvent` added.

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_endpoint_fanout.py -v` → FAIL
- [ ] **Step 3: Implement** list fetch, derived keys, response `delivery_ids`, ADR-010
- [ ] **Step 4:** PASS + `make ci`
- [ ] **Step 5: Do not commit**

---

### Task 1: JSON Schema registry stub (PostgreSQL)

**Files:**
- Create: `app/domain/models/payload_schema.py`, `app/domain/services/schema_registry.py`, `alembic/versions/20260602_0005_payload_schemas.py`, `tests/unit/test_schema_registry.py`
- Modify: inbound + internal outbound handlers to validate when a row exists; `pyproject.toml` add `jsonschema>=4.23,<5`; Admin `POST/GET /admin/v1/schemas` (`RequireAdmin` write, `RequireViewer` read)
- Optional seed: schema for `order.created` with required `order_id`

**Table `payload_schemas`:** PK UUIDv7 (not BIGINT). Columns: `event_type VARCHAR(128)`, `version INTEGER`, `json_schema JSONB`, `status VARCHAR(16)` (`active`/`deprecated`), UNIQUE `(event_type, version)`. Never expose sequential ids.

**Interfaces:**

```python
def validate_payload(event_type: str, payload: dict, schema_row: PayloadSchema | None) -> None:
    """No schema → accept. Invalid → raise SchemaValidationError."""
```

Inbound/outbound: 422 `payload does not match registered schema` when active schema exists and fails. No Confluent client.

**Acceptance:** missing schema → accept; invalid `order.created` → 422; dual-id safe; `make ci`

**Steps:** failing unit (no schema vs invalid vs valid) → Alembic + service → wire handlers → `make ci`. Do not commit.

---

### Task 2: Replay approval flow

**Files:**
- Create: `app/domain/models/replay_approval.py`, `app/domain/enums.py` (`ReplayApprovalStatus`: `pending`/`approved`/`rejected`), `app/api/v1/admin/replay_approvals.py`, `alembic/versions/20260602_0006_replay_approvals.py`, `tests/unit/test_replay_approval.py`
- Modify: `POST /admin/v1/deliveries/{id}/replay` when `HUB_REPLAY_APPROVAL_REQUIRED=true` (default **true** in Compose, overridable in tests): operator creates pending row, **does not** transition delivery; `POST /admin/v1/replay-approvals/{id}/approve` `RequireAdmin` calls existing `replay_delivery`; reject audited

**Table:** PK UUIDv7; `delivery_id BIGINT` FK; `reason TEXT`; `requested_by`; `approved_by` NULL; `status`; timestamps. `resource_id` in audit = delivery **public_id**.

When setting is false, keep today’s immediate replay (tests stay green).

**Acceptance:**
- Operator replay with approval on → 202 `{approval_id, status:"pending"}`; delivery still `failed`
- Viewer approve → 403
- Admin approve → delivery `replaying` + outbox + audit `delivery.replay` and `replay.approve`
- Empty reason → 422
- `make ci`

**Steps:** TDD pending/approve/reject → wire setting → OpenAPI responses 401/403/404/409/422. Do not commit.

---

### Task 3: Partner status API

**Files:**
- Create: `app/api/v1/partner/deliveries.py`, `app/api/deps_partner.py` (API key auth reuse inbound lookup), `tests/unit/test_partner_status_api.py`
- Modify: `app/main.py` include router; OpenAPI tag `partner` (own deliveries only; not admin)

`GET /partner/v1/deliveries/{id}` — `{id}` is delivery **public_id**. Auth: `Authorization: Bearer <api_key>` like inbound. Scope must include `status:read` (seed keys that only have `inbound:write` → 403). Other partner’s delivery → **404** (no leak). Response: status, attempt_count, last_error_code, sla_breached, first_success_at — **no** full payload (or mask to `{"_masked": true}`), no BIGINT.

**Acceptance:** OpenAPI Field descriptions; 401/403/404; `make ci`

---

### Task 4: W3C `traceparent` on Kafka

**Files:**
- Create: `app/observability/trace_context.py`, `tests/unit/test_kafka_traceparent.py`
- Modify: `app/integrations/kafka_producer.py` header injection; `app/workers/outbox_relay.py` and outbound consumer extract + `TraceContextTextMapPropagator`

**Interfaces:**

```python
def kafka_trace_headers() -> list[tuple[str, bytes]]: ...
def extract_traceparent(headers: list[tuple[str, bytes]] | None) -> None: ...
```

Use OpenTelemetry `TraceContextTextMapPropagator` (W3C). Header name `traceparent`. Do not put `trace_id` on Prometheus attributes. Message key still partner `public_id`.

Sources: https://www.w3.org/TR/trace-context/ and OpenTelemetry Python context propagation docs.

**Acceptance:** produced headers include `traceparent` when a span is active; consumer attaches context; unit test with in-memory propagator; `make ci`

---

### Task 5: Weekly compliance export

**Files:**
- Modify: `app/api/v1/admin/analytics.py` `GET /admin/v1/analytics/compliance-export?from=&to=` `RequireViewer`, `text/csv` or JSON (`Accept`); `tests/unit/test_compliance_export.py`
- Optional Celery beat weekly **notify/log only** (no webhook POST) — if added, reuse `celery_app/tasks/` notify pattern from Stage 2 rotation task

CSV columns: `partner_slug,success_rate,sla_compliance_pct,sla_breaches,dlq_count` for the window. IDs = slug only (not UUID labels in the file if used as metrics; public UUID of partner as `partner_id` column is OK).

**Acceptance:** empty window → header-only CSV; dual-id safe; `make ci`

---

### Task 6: k6 vs documented p95

**Files:**
- Create: `load/k6/outbound_ingest.js`, `docs/perf/README.md`, `docs/perf/outbound-ingest.md`
- Modify: `Makefile` `load-k6` target (not part of `make ci`)

Script: `POST http://localhost:8000/internal/v1/outbound/events` with admin token, UUIDv7 correlation, unique idempotency keys, threshold `http_req_duration{expected_response:true} p(95)<2000` (document actual). Record **POST → expected status** (202). Do not claim 2M/day without a measured run.

Use `grafana/k6` via `docker run --network host` or compose profile `load`. If Compose network: `K6_BASE_URL=http://hub-api:8000`.

**Acceptance:** `docs/perf/outbound-ingest.md` has command + last run p95 **or** “not run in this session” with the exact command; script exists; `make ci` still Python-only.

---

### Task 7: HA Kafka docs + replica docs + missing runbooks

**Files:**
- Modify: `docs/architecture.md` (prod Kafka RF=3, 3 brokers, consumer groups; Compose stays 1 broker RF=1; optional PG replica for Admin **list** only, writes on primary)
- Create: `docs/runbooks/outbox-lag.md`, `docs/runbooks/secret-rotation.md` (Stage 2 leftovers; operator English)
- Modify: `README.md` GitHub-grade (operator English; no gitignored-path links); `AGENTS.md` §0.2 runbook list; `docs/slo.md` if needed

**Acceptance:** operator README has no gitignored-path links; no three-broker Compose.

---

### Task 8: Thin admin UI — sandbox + approval queue

**Files:**
- Modify: `admin_ui/src/` — sandbox test POST via existing `POST /admin/v1/deliveries/test` **or** if that route is missing, add backend in this task (spec §7.1.3 `POST /admin/v1/deliveries/test` `RequireAdmin`) plus UI form; approval list calling Task 2 APIs
- Exhaustive `switch` + `never` for approval status
- Replay/approve disabled until trimmed reason
- Talks **only** to `/admin/v1`

**Acceptance:** Docker UI build; no HMAC/outbox/CB in browser; `make ci` (Python) green

---

### Task 9: Stage 3 evidence template + orchestrator live prove

**Files:**
- Create: local gitignored Stage 3 DoD evidence checklist (k6 command, partner status curl, fan-out two URLs, approval path, Jaeger Kafka continuation if Task 4 live, `make ci`)
- Do **not** write “Stage 3 Done”

**Acceptance:** template exists; orchestrator fills after live commands.

---

## Self-review (orchestrator)

- Spec §3.5 rows map to tasks 0–9.
- UNIQUE `(partner_id, idempotency_key)` preserved via derived keys (ADR-010).
- No TBD / “similar to Task N” / git commit steps.
- Helm/kind optional and out of this plan.
- Worker still may produce retry/DLQ Kafka after attempt commit.
