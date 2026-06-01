# Partner Integration Hub — Stage 1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Implementer ≠ Reviewer. Local: no push. **Do not git commit.** Checklists `- [ ]`.
> Execute per [`AGENTS.md` §10.2](../../AGENTS.md). Role prompts: [`docs/agentic/role-prompts/`](../agentic/role-prompts/).

**Goal:** Ship a Compose-demoable MVP: inbound HMAC + idempotency, internal outbound, one retry tier (`hub.outbound.retry.30s`), DLQ, audited single replay, thin admin UI, OTel → Collector → Prometheus + Jaeger, live `/docs` bar, `make seed` / `make seed-prod-like`.

**Architecture:** Single FastAPI package `app/`. PostgreSQL SoT; Kafka bus (`aiokafka`); Redis idempotency cache. Stage 1 publish-after-commit + `hub_outbox_discrepancy_total`. Workers consume `hub.outbound.pending` and `hub.outbound.retry.30s`. Partner mock on :8090. Admin UI talks only to `/admin/v1`.

**Tech Stack (as of plan date 2026-06-01; current floor in `spec.md` §5):** Python 3.12+, uv, FastAPI 0.115+, Uvicorn 0.30+, Pydantic v2.8+, SQLAlchemy 2 async + asyncpg, Alembic 1.13+, PostgreSQL 16, Redis 7.2, Kafka 3.7+ KRaft, aiokafka, httpx 0.27+, structlog 24+, OpenTelemetry SDK + OTLP exporter, cryptography (Fernet), argon2-cffi, uuid6/uuid7 lib, pytest 8, pytest-asyncio, ruff 0.5+, mypy 1.10+ strict on `app/` and `celery_app/`. UI: Vite + React + TypeScript. Images: `python:3.12-slim-bookworm`, non-root USER.

## Global Constraints

- Dual-id only on `partners` and `deliveries`; sequential BIGINT never in DTO/OpenAPI/UI/Kafka/replay (ADR-009).
- At-least-once; no exactly-once claims (ADR-001).
- Kafka retries, not Celery transport (ADR-002). Celery package may exist as a stub; no webhook POST from Celery in Stage 1.
- Kafka client **aiokafka**; message key = partner `public_id` (ADR-003).
- HMAC-SHA256 on raw body + `timestamp.`; skew >300s → 403; `hmac.compare_digest` (ADR-004).
- Stage 1 outbox: publish-after-commit + discrepancy metric only (ADR-007). No silent dual-write pretend.
- SLA clock stops at `first_success_at` (ADR-008). Replay does not mutate payload.
- Thin UI: no HMAC/retry/outbox/CB in the browser (ADR-006).
- OTel OTLP to Collector; traces → **Jaeger**; metrics → Prometheus. Not Tempo. Metric attributes: `partner_slug` not UUIDs.
- Correlation: UUIDv7; generate if missing; invalid → 422; header `X-Correlation-Id` (accept `X-Correlation-ID`).
- Pagination: `limit` + `offset` only (default 50, max 200).
- Poison: 400/401/403/404/422 → failed+DLQ immediately; 408/429/5xx/network → retry. After DLQ, commit Kafka offset.
- OpenAPI: title `Partner Integration Hub`; summary+description; tags inbound/admin/internal/health; servers `http://localhost:8000`; Field descriptions; enums not bare `str`; POST examples; documented error responses the handler actually raises.
- Frozen ports: API 8000, UI 8080, PG 5432, Redis 6379, Kafka 9092, OTLP 4317/4318, Prom 9090, Grafana 3000, Jaeger 16686, mock 8090.
- Version floors from spec §5. Appendix A env names exact.
- Empty `OTEL_SDK_DISABLED=` must not crash bool parse.
- No git commit/push. No OFOM/billing/SSO invariant copy-paste. No SOAP/Portal/mesh.
- Imports at module top. No `Task N` in application code after the task ships.

---

## File map (Stage 1)

| Path | Responsibility |
|------|----------------|
| `pyproject.toml`, `Makefile`, `.env.example` | Tooling and demo env |
| `app/config.py` | Pydantic Settings (Appendix A + URLs + OTEL + FERNET + ADMIN token) |
| `app/logging.py` | structlog JSON + correlation / public ids / trace_id when span exists |
| `app/observability/otel.py` | MeterProvider + TracerProvider OTLP to Collector |
| `app/main.py` | `create_app()` factory, OpenAPI metadata, lifespan |
| `app/domain/enums.py` | Partner/delivery/endpoint/dead-letter statuses |
| `app/domain/services/hmac_service.py` | Sign + verify |
| `app/domain/services/backoff.py` | Delay + jitter |
| `app/domain/services/status_machine.py` | Valid transitions + invalid metric hook |
| `app/domain/services/sla_service.py` | first_success / sla_breached |
| `app/domain/models/*.py` | SQLAlchemy 2 mapped classes |
| `app/api/v1/{inbound,admin,internal}` | HTTP |
| `app/api/middleware/correlation.py` | UUIDv7 header |
| `app/workers/outbound_consumer.py` | pending + retry.30s |
| `app/integrations/http_client.py` | httpx timeouts, no lib retries |
| `app/integrations/kafka_producer.py` | aiokafka producer |
| `partner_mock/` | FastAPI chaos profiles |
| `admin_ui/` | Thin SPA |
| `tests/unit/test_openapi_docs.py` | `/docs` quality lock |
| `infra/otel/collector.yaml` | OTLP → Prom + Jaeger |
| `scripts/seed_partners.py`, `scripts/seed_prod_like.py` | Idempotent upserts |
| `scripts/generate_openapi.py` | Snapshot exporter |

---

## Spec §3.3 Must → tasks

| Must | Tasks |
|------|-------|
| uv / Makefile / env | 0 |
| Compose PG/Redis/Kafka + OTel + Prom + Jaeger + Grafana + mock | 1–2 |
| HMAC / backoff / status machine unit tests | 4–5 |
| Dual-id models + Alembic | 6 |
| Inbound HMAC + idempotency 202/200 | 9 |
| Internal outbound | 10 |
| Admin list/get/replay + audit | 11 |
| Worker pending + retry.30s + DLQ | 12 |
| Fault injection subset §10.3 | 13 |
| Thin UI | 14 |
| Grafana overview + seed | 15 |
| OpenAPI live bar + unit lock + CI | 0, 7, 16 |
| SLA field + metric (simplified) | 5, 10, 12 |
| Publish-after-commit + discrepancy | 10 |

---

### Task 0: uv project, Makefile, settings stub, OpenAPI lock, health app

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.env.example`, `app/__init__.py`, `app/config.py`, `app/main.py`, `tests/unit/test_openapi_docs.py`, `tests/unit/test_settings_otel_disabled.py`, `scripts/generate_openapi.py`, `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.pre-commit-config.yaml`
- Modify: `README.md` only if Makefile targets need a one-line pointer
- Test: `tests/unit/test_openapi_docs.py`, `tests/unit/test_settings_otel_disabled.py`

**Interfaces:**
- Consumes: nothing
- Produces: `create_app() -> FastAPI`; `get_settings() -> Settings`; `make ci`, `make export-openapi`, `make lint`, `make typecheck`, `make test-unit`

**Spec:** §5, §9, Appendix A; prompt §2.1 OpenAPI bar; §8.2 settings

**Acceptance:**
- `uv sync` works
- `make ci` = lint + typecheck + test-unit, exit 0
- OpenAPI `info.title` is `Partner Integration Hub`; summary and description non-empty; tags inbound, admin, internal, health each with description; `servers` includes `http://localhost:8000`
- Every component schema property except ValidationError/HTTPValidationError has `description`
- `GET /docs` via TestClient returns 200 HTML containing swagger
- Empty `OTEL_SDK_DISABLED` does not crash settings
- `.env.example` lists Appendix A names + `DATABASE_URL`, `REDIS_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `FERNET_KEY`, `ADMIN_BOOTSTRAP_TOKEN`, `LOG_LEVEL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_SDK_DISABLED`
- No git commit

**Steps:**

- [ ] **Step 1: Write failing OpenAPI tests** (before a complete `create_app`)

```python
# tests/unit/test_openapi_docs.py
from fastapi.testclient import TestClient

from app.main import create_app

SKIP_SCHEMA = {"ValidationError", "HTTPValidationError"}


def test_openapi_info_and_tags() -> None:
    spec = create_app().openapi()
    assert spec["info"]["title"] == "Partner Integration Hub"
    assert spec["info"].get("summary")
    assert spec["info"].get("description")
    names = {t["name"] for t in spec["tags"]}
    assert {"inbound", "admin", "internal", "health"} <= names
    for tag in spec["tags"]:
        assert tag.get("description")


def test_schema_properties_have_descriptions() -> None:
    spec = create_app().openapi()
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if name in SKIP_SCHEMA:
            continue
        for prop_name, prop in schema.get("properties", {}).items():
            assert "description" in prop, f"{name}.{prop_name} missing description"


def test_docs_html() -> None:
    client = TestClient(create_app())
    res = client.get("/docs")
    assert res.status_code == 200
    assert "swagger" in res.text.lower()


def test_no_sequential_id_on_partner_delivery_schemas() -> None:
    spec = create_app().openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    for key, schema in schemas.items():
        if "Partner" in key or "Delivery" in key:
            props = schema.get("properties", {})
            if "id" in props:
                t = props["id"].get("type")
                fmt = props["id"].get("format")
                assert t != "integer", f"{key}.id must not be integer BIGINT"
                if t == "string":
                    assert fmt in {"uuid", None} or "uuid" in str(props["id"]).lower()
```

- [ ] **Step 2: Run tests — expect FAIL** (import or assertion)

Run: `uv run pytest tests/unit/test_openapi_docs.py -v`  
Expected: FAIL (module not found or title not set)

- [ ] **Step 3: Minimal `create_app` + Settings + Makefile + pyproject**

`Settings` uses pydantic-settings. For `OTEL_SDK_DISABLED`, coerce empty string to `False` (sibling gotcha). OpenAPI:

```python
from fastapi import FastAPI

OPENAPI_TAGS = [
    {"name": "inbound", "description": "Partner-facing webhook ingest. HMAC-SHA256 over timestamp and raw body; API key in Authorization."},
    {"name": "admin", "description": "Operator API. Identifiers are UUIDv7 public ids only. Replay requires reason and is audited."},
    {"name": "internal", "description": "Platform services only. Not partner-facing. partner_id is the partner public UUID."},
    {"name": "health", "description": "Liveness and readiness probes."},
]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Partner Integration Hub",
        summary="At-least-once B2B webhook delivery with HMAC, retries, DLQ, and audited replay.",
        description=(
            "Inbound uses HMAC-SHA256 (`X-Hub-Signature-256`) over `{timestamp}.{raw_body}` "
            "and Bearer API keys. First accept returns **202**; duplicate Idempotency-Key returns **200**. "
            "JSON `id` fields are UUIDv7 public identifiers, never sequential database keys. "
            "Correlation `X-Correlation-Id` is UUIDv7. Internal outbound is tagged `internal` and is not partner-facing."
        ),
        openapi_tags=OPENAPI_TAGS,
        servers=[{"url": "http://localhost:8000", "description": "Local Compose"}],
        contact=None,
        license_info=None,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    # health routes registered here or via router — tag health
    return app
```

Include `GET /inbound/v1/health` and `GET /internal/v1/health` liveness (200 `{status: ok}`) so tags have operations. Use Literal/Enum for any status field.

Makefile targets: `ci`, `lint`, `typecheck`, `test-unit`, `test-integration`, `test-e2e`, `test-contract`, `compose-up`, `compose-down`, `compose-logs`, `seed`, `seed-prod-like`, `export-openapi`, `migrate`. Seed/compose may stub `echo not ready` **only** until Tasks 1/15 — prefer empty scripts that exit 0 with a message, not silent success pretending seed ran.

`.env.example` demo secrets clearly marked non-prod. Generate a **sample** Fernet key labeled demo-only.

CI workflow: ruff, mypy, `pytest tests/unit`. Dependabot for pip and github-actions weekly.

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/unit/test_openapi_docs.py tests/unit/test_settings_otel_disabled.py -v && make ci`  
Expected: PASS, exit 0

**Sources consulted (implementer must cite):** FastAPI metadata, Pydantic settings, uv.

---

### Task 1: Compose data plane — Postgres 16, Redis 7.2, Kafka KRaft, topics script

**Files:**
- Create: `docker-compose.yml` (data services + healthchecks), `infra/kafka/create-topics.sh`, `docker-compose.test.yml` (or document testcontainers for later integration)
- Test: `tests/unit/test_compose_ports.py` **or** a shell assertion in the report that published ports match AGENTS §1.1 (prefer a small unit test that parses compose YAML for host ports)

**Interfaces:**
- Consumes: frozen ports from AGENTS
- Produces: healthy `postgres`, `redis`, `kafka` on Compose network; topics created by `create-topics.sh`

**Spec:** §5, §7.2, §9; prompt §6

**Acceptance:**
- Kafka **KRaft, no ZooKeeper**
- Host ports: 5432, 6379, 9092 only for this task
- `depends_on` with `condition: service_healthy`
- `restart: unless-stopped`; json-file logs `max-size: 10m`
- Topics at least: `hub.outbound.pending`, `hub.outbound.retry.30s`, `hub.outbound.dlq`, `hub.inbound.order.created`, `hub.inbound.order.updated`, `hub.integration.sla_breached`
- Partitions may be 1 locally
- No app images required yet

**Steps:**

- [ ] **Step 1: Write port-lock test**

```python
# tests/unit/test_compose_ports.py
from pathlib import Path
import yaml

EXPECTED = {
    "postgres": "5432:5432",
    "redis": "6379:6379",
    "kafka": "9092:9092",
}

def test_frozen_data_plane_ports() -> None:
    data = yaml.safe_load(Path("docker-compose.yml").read_text())
    services = data["services"]
    for name, mapping in EXPECTED.items():
        ports = services[name].get("ports") or []
        flat = [p if isinstance(p, str) else str(p) for p in ports]
        assert any(mapping.split(":")[0] in str(p) for p in flat), name
```

- [ ] **Step 2:** `uv run pytest tests/unit/test_compose_ports.py -v` → FAIL (no compose)
- [ ] **Step 3:** Implement compose + `create-topics.sh` (kafka-topics.sh in a one-shot service `kafka-init` depending on kafka healthy)
- [ ] **Step 4:** GREEN pytest; in report note whether `docker compose up -d postgres redis kafka` was run (if Docker unavailable, say BLOCKED for live health but keep files)

Do not add Tempo. Do not add ZooKeeper.

---

### Task 2: OTel Collector, Prometheus, Jaeger, Grafana stubs, partner-mock skeleton

**Files:**
- Create: `infra/otel/collector.yaml`, `infra/prometheus/prometheus.yml`, `infra/prometheus/alerts.yml`, `infra/grafana/provisioning/datasources/datasource.yml`, `infra/grafana/provisioning/dashboards/dashboards.yml`, `docs/grafana/dashboards/integration_health.json` (minimal panels OK), `docs/grafana/dashboards/dlq_replay.json` (stub), `docs/grafana/dashboards/sla_compliance.json` (stub panel Stage 1), `partner_mock/app.py` (or `partner_mock/main.py`), `partner_mock/Dockerfile`, Grafana + Jaeger + collector + mock + prometheus services in `docker-compose.yml`
- Modify: `docker-compose.yml`, `tests/unit/test_compose_ports.py` (add 4317, 4318, 9090, 3000, 16686, 8090)
- Test: compose port test; `tests/unit/test_partner_mock_scenarios.py` if mock is importable without Docker

**Interfaces:**
- Collector: OTLP gRPC 4317 / HTTP 4318 → prometheus exporter + otlp/jaeger exporter
- Mock: `X-Mock-Scenario: ok|fail_503|fail_400|timeout|fail_429` or path-based profiles; optional signature verify later (Task 16)

**Spec:** §8.5, §9; prompt §2.4, §11

**Acceptance:**
- Apps will talk **only** OTLP to collector (no vendor SDK)
- Jaeger UI :16686; Grafana :3000 demo admin/admin documented as demo-only
- Alert `HubDLQGrowth` with `runbook_url` to `docs/runbooks/dlq-response.md`
- Markdown/text panel note: idle `rate()` NaN is not an outage; poison 4xx ≠ infra
- Mock FastAPI (not WireMock)
- App containers not required yet; collector/prom/jaeger/grafana/mock should have healthchecks where the image supports them

**Steps:** TDD port test FAIL → add services GREEN. Partner mock: test that unknown scenario defaults to 200; `fail_400` returns 400; `fail_503` returns 503.

Jaeger: native OTLP. **Not Tempo.**

---

### Task 3: structlog JSON + OTel SDK bootstrap

**Files:**
- Create: `app/logging.py`, `app/observability/otel.py`, `app/observability/metrics.py` (instrument names from spec §8.5.2 + `hub_outbox_discrepancy_total`, `hub_invalid_transition_total`)
- Modify: `app/main.py` lifespan: setup/shutdown OTel (flush BatchSpanProcessor / PeriodicExportingMetricReader)
- Test: `tests/unit/test_logging_redaction.py`, `tests/unit/test_otel_resource.py`

**Interfaces:**
- `configure_logging(settings) -> None`
- `configure_otel(service_name: str, settings) -> None` — no-op if `OTEL_SDK_DISABLED`
- Resource: `service.name`, `service.version`, `deployment.environment`
- Log fields spec §8.5.1 + `trace_id`/`span_id` when a span exists; redact `authorization`, `x-hub-signature-256`, secrets

**Spec:** §8.5.1–8.5.2; prompt §2.4

**Acceptance:**
- Empty `OTEL_SDK_DISABLED` already handled in settings (Task 0)
- No `prometheus_client` registry dual-registering the same series
- No `app.mount("/metrics")`
- Cardinality: helpers must not accept `delivery_id` as a metric attribute (unit test)

**Steps:** Write tests that logging filter redacts Bearer tokens and that `record_delivery_metric` rejects high-card keys → FAIL → implement → GREEN.

Official OTel Python exporter docs: OTLP to Collector.

---

### Task 4: HMAC-SHA256 service (TDD)

**Files:**
- Create: `app/domain/services/hmac_service.py`, `tests/unit/test_hmac.py`

**Interfaces:**
- `signed_payload(timestamp: str, body: bytes) -> bytes`  # `f"{timestamp}.".encode() + body`
- `sign(secret: bytes | str, timestamp: str, body: bytes) -> str`  # `sha256=<hex>`
- `verify(secret: bytes | str, timestamp: str, body: bytes, header: str, *, now: int, tolerance: int = 300, previous_secret: bytes | str | None = None) -> bool`
- Raises or returns a result type for skew vs mismatch — prefer a small enum/error: `skew` vs `mismatch` so API can map both to 403 but tests distinguish

**Spec:** §7.1.1, §7.5, ADR-004

**Acceptance:**
- Valid signature true
- Tampered body false
- Skew > 300 false
- Previous secret accepted when primary fails (Stage 2 rotation; implement now, used later)
- Uses `hmac.compare_digest`
- Does not JSON-reencode body

**Steps:**

```python
def test_valid_signature() -> None:
    body = b'{"a":1}'
    ts = "1720000000"
    sig = sign("secret", ts, body)
    assert verify("secret", ts, body, sig, now=1720000000) is True

def test_skew_rejected() -> None:
    body = b"{}"
    ts = "1"
    sig = sign("secret", ts, body)
    assert verify("secret", ts, body, sig, now=100000, tolerance=300) is False

def test_previous_secret() -> None:
    body = b"{}"
    ts = "100"
    sig = sign("old", ts, body)
    assert verify("new", ts, body, sig, now=100, previous_secret="old") is True
```

RED then GREEN. Cite Python `hmac` docs.

---

### Task 5: Backoff, status machine, SLA clock (TDD)

**Files:**
- Create: `app/domain/enums.py`, `app/domain/services/backoff.py`, `app/domain/services/status_machine.py`, `app/domain/services/sla_service.py`
- Test: `tests/unit/test_backoff.py`, `tests/unit/test_status_machine.py`, `tests/unit/test_sla.py`

**Interfaces:**
- `DeliveryStatus` enum: `pending`, `delivering`, `delivered`, `retrying`, `failed`, `replaying`
- `can_transition(src, dst) -> bool`
- `transition(src, dst) -> DeliveryStatus` — invalid: call `on_invalid` hook / increment path for `hub_invalid_transition_total` (injectable callable)
- `compute_delay_seconds(attempt_number: int, *, base=30, multiplier=2, max_seconds=3600, jitter_pct=0.1, rng=...) -> float` — formula spec §6.6; jitter within ±jitter_pct; delay without jitter capped at max
- `apply_first_success(now, sla_deadline_at, first_success_at, sla_breached) -> tuple[datetime, bool]` — set first_success only if empty; set sla_breached once if now > deadline
- `deadline_passed_while_open(now, sla_deadline_at, first_success_at, sla_breached) -> bool` — flag when deadline passes before success

**Spec:** §6.5, §6.6, ADR-008

**Acceptance:**
- All valid edges from spec table; invalid pending→failed is invalid (must go through delivering) — follow spec table strictly
- `failed → replaying` valid; `delivered → pending` invalid
- Jitter bounds: for delay D, result in `[D*(1-j), D*(1+j)]` after adding jitter to delay (test with rng=lambda: 0.0 and 1.0 mapped to uniform)
- SLA: first 2xx fills `first_success_at`; second 2xx does not change it; breach flips once

**Steps:** Write the three test modules first, run FAIL, implement, GREEN.

---

### Task 6: SQLAlchemy models + first Alembic revision (dual-id)

**Files:**
- Create: `alembic.ini`, `alembic/env.py` (async), `app/db/session.py`, `app/db/base.py`, `app/domain/ids.py`, `app/domain/services/secrets.py`, `app/domain/models/partner.py`, `endpoint.py`, `delivery.py`, `attempt.py`, `dead_letter.py`, `inbound_event.py`, `audit.py`, `api_key.py`, `outbox.py`, `app/domain/models/__init__.py`, `alembic/versions/20260601_0001_initial_dual_id.py`
- Modify: `pyproject.toml` (sqlalchemy, asyncpg, alembic, uuid6, cryptography, argon2-cffi), `Makefile` (`migrate`), `app/domain/enums.py` (partner/endpoint/dead-letter enums)
- Test: `tests/unit/test_models_dual_id.py` (mapper columns/uniques/indexes — no live DB)

**Interfaces:**
- `Partner.id: Mapped[int]` PK Identity; `public_id: Mapped[UUID]`; `slug` unique; Stage 1 `signing_secret_encrypted: Mapped[bytes | None]`
- `Delivery.id: Mapped[int]`; `public_id`; UNIQUE `(partner_id, idempotency_key)`
- `generate_uuidv7() -> UUID` in `app/domain/ids.py` (uuid6)
- `encrypt_signing_secret` / `decrypt_signing_secret` in `app/domain/services/secrets.py`
- `get_sessionmaker(settings) -> async_sessionmaker[AsyncSession]` with `expire_on_commit=False`

**Spec:** §6.3–6.4, ADR-009

**Acceptance:**
- Dual-id only `partners` + `deliveries`; BIGINT FK to dual-id tables; UUID PK satellites; `outbox_events` BIGINT PK without `public_id`
- Natural UNIQUE not composite PK; `audit_logs.resource_id` UUID
- Indexes §6.4 including GIN `event_types`, outbox `(published_at NULLS FIRST, created_at)`
- Enums stored as strings; no `create_all` on Compose/API path — Alembic only
- `make migrate` → `uv run alembic upgrade head`
- Host migrate: `DATABASE_URL=postgresql+asyncpg://hub:hub@localhost:5432/hub` (settings default hostname `postgres` for Compose network)

**Steps:**

- [x] **Step 1: Write failing column/index tests** (`tests/unit/test_models_dual_id.py` — 14 tests on mapped metadata)

Run: `uv run pytest tests/unit/test_models_dual_id.py -v`  
Expected: FAIL (import / missing models)

- [x] **Step 2: Add deps** (`sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `uuid6`, `cryptography`, `argon2-cffi`)

- [x] **Step 3: Implement** `app/db/base.py` (`Base`, `DualIdMixin`, `UuidPrimaryMixin`), models, `ids.py`, `secrets.py`, `session.py`, async `alembic/env.py`, revision `20260601_0001_initial_dual_id.py`

- [x] **Step 4: GREEN** — `make ci` exit 0 (101 unit tests)

- [x] **Step 5: Migrate** — `DATABASE_URL=postgresql+asyncpg://hub:hub@localhost:5432/hub uv run alembic upgrade head` → revision `20260601_0001` applied

**Sources consulted (implementer must cite):**
- [SQLAlchemy 2.0 — asyncio extension](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html): `create_async_engine`, `async_sessionmaker`, `expire_on_commit=False`, `run_sync` for Alembic bridge
- [SQLAlchemy 2.0 — mapped columns / DeclarativeBase](https://docs.sqlalchemy.org/en/20/orm/mapping_api.html): `Mapped`, `mapped_column`, `Identity()` for BIGINT PK
- [Alembic — async migrations](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic): `async_engine_from_config`, `connection.run_sync(do_run_migrations)`, `asyncio.run` in `env.py`
- [Alembic — operation reference](https://alembic.sqlalchemy.org/en/latest/ops.html): `op.create_table`, `op.create_index` with `postgresql_using='gin'` and `postgresql_ops={'published_at': 'NULLS FIRST'}`

---

### Task 7: FastAPI middleware — correlation UUIDv7, max body, auth stubs, OpenAPI examples on health/docs still green

**Files:**
- Create: `app/api/middleware/correlation.py`, `app/api/middleware/max_body.py`, `app/api/deps.py`, `app/api/v1/health.py`
- Modify: `app/main.py` — CORS `http://localhost:8080`; lifespan engine/redis/kafka **lazy stubs** OK if connect on first use; do not require Kafka up for unit tests
- Test: `tests/unit/test_correlation.py`, keep `test_openapi_docs.py` green

**Interfaces:**
- Missing correlation → generate UUIDv7, echo `X-Correlation-Id`
- Invalid (not UUID or not version 7) → **422**
- Accept `X-Correlation-ID` case variant
- Max body Stage 1: 256 KB → 413 or 422 (pick one, document in OpenAPI `responses=`, test it)

**Spec:** prompt §2.5; §8.1 payload max

**Acceptance:** OpenAPI still has tags/summary; correlation tests pass.

---

### Task 8: Admin partners, endpoints, API keys (hashed), signing secret once

**Files:**
- Create: `app/schemas/partner.py`, `app/api/v1/admin/partners.py`, `app/api/v1/admin/endpoints.py`, `app/api/auth.py` (bootstrap token / HS256 JWT stub, roles)
- Test: `tests/unit/test_partner_schemas.py`; `tests/integration/test_admin_partners.py` (may mark integration; if no Docker, use httpx ASGI + SQLite **not** preferred — use pytest-asyncio + testcontainers **or** skip integration with `pytest.mark.integration` and still have schema/auth unit tests)

**Interfaces:**
- Paths exact spec §7.1.2; `{id}` = public UUID
- Create partner returns public id, never BIGINT
- `POST .../api-keys` returns plaintext **once** + prefix; store argon2 hash
- Admin: `Authorization: Bearer <ADMIN_BOOTSTRAP_TOKEN>` **or** JWT issued from it; `hub_viewer` cannot POST partners
- List: `limit` `offset`

**Spec:** §7.1.2, §2.2, ADR-009

**Acceptance:** OpenAPI Field descriptions on new schemas; `responses=` for 401/403/404/422; POST `Body(openapi_examples=...)`. RBAC: viewer GET ok, POST 403.

Prefer integration test with TestClient + in-memory? SQLAlchemy async often needs PG. **Use testcontainers PostgreSQL** if Docker available; else unit-test serializers + a faked repository. Report which.

---

### Task 9: Inbound events — HMAC, API key, idempotency 202/200, persist, publish

**Files:**
- Create: `app/api/v1/inbound/events.py`, `app/domain/services/idempotency.py`
- Modify: kafka producer
- Test: `tests/unit/test_inbound_hmac_api.py` with TestClient and dependency overrides; `tests/integration/test_inbound_idempotency.py` when PG+Redis+Kafka exist

**Interfaces:**
- `POST /inbound/v1/{partner_slug}/events`
- Headers exact spec §7.1.1
- 202 `{event_id, status: "accepted"}`; duplicate 200 `{event_id, status: "duplicate"}`
- 401 bad key; 403 bad HMAC/skew; 429 rate limit (Redis token bucket; if Redis down, fail-open inbound **except** DB UNIQUE still holds — document)
- Publish `hub.inbound.{event_type}` for `order.created` / `order.updated` (reject unknown types 422)
- One Kafka message on duplicate (integration)

**Spec:** §7.1.1, J5

**Acceptance:** OpenAPI examples: happy inbound, bad timestamp. Rate limit unit test with fake Redis.

---

### Task 10: Internal outbound — create delivery, snapshot SLA, publish-after-commit, discrepancy metric

**Files:**
- Create: `app/api/v1/internal/outbound.py`, `app/domain/services/delivery_service.py`
- Test: `tests/unit/test_delivery_create.py`, OpenAPI example `order.created`

**Interfaces:**
- `POST /internal/v1/outbound/events` body `{ partner_id, event_type, payload, idempotency_key, correlation_id }` where `partner_id` is **public_id**
- Resolve endpoint by partner + event_type (Stage 1: one outbound URL)
- Snapshot `max_attempts`, `sla_deadline_at`
- Duplicate idempotency: return existing delivery public id (200 or 202 — pick and document; inbound-style 200 duplicate is OK for outbound too if you document it; spec inbound is explicit; for internal prefer 200 duplicate vs 202 new)
- After commit, produce `hub.outbound.pending` key=partner public_id, envelope `schema_version: 1`, headers correlation_id, delivery_id (public), event_type, attempt, content-type
- On produce failure: increment `hub_outbox_discrepancy_total`, log, **do not** hide the delivery (API still 202 with delivery id) — operator can replay later

**Spec:** §7.1.5, §7.3, ADR-007 Stage 1

**Acceptance:** No BIGINT in response. Envelope IDs are UUIDv7.

---

### Task 11: Admin deliveries, attempts, single replay + audit; DLQ list

**Files:**
- Create: `app/api/v1/admin/deliveries.py`, `app/api/v1/admin/dead_letters.py`, `app/domain/services/replay_service.py`
- Test: `tests/unit/test_replay_reason.py`, `tests/integration/test_replay_audit.py`

**Interfaces:**
- GET list filters: `partner_id`, `status`, `event_type`, `from`, `to`, `correlation_id`, `sla_breached`; pagination limit/offset
- GET `{id}` + attempts
- POST `{id}/replay` body `{ reason: str, reset_attempt_counter: bool = false }` — **422 if reason missing/empty**
- Replay: `failed → replaying`, same payload, same Idempotency-Key, publish pending (or retry topic); insert `audit_logs` with resource_id = delivery public_id, action `delivery.replay`
- GET `/admin/v1/dead-letters`
- Stage 1: ack/purge may 501 or implement GET-only + replay path; prefer GET list now; ack/purge Stage 2 Must — if you implement ack in S1, audit it

**Spec:** §7.1.3, J4

**Acceptance:** viewer cannot replay (403). operator can. OpenAPI 404/403/422.

---

### Task 12: Outbound worker — HTTP POST, poison vs retry, DLQ

**Files:**
- Create: `app/workers/outbound_consumer.py`, `app/integrations/http_client.py`, `app/domain/services/delivery_attempt.py`, Dockerfile for API/worker (same image, different CMD)
- Modify: `docker-compose.yml` — `hub-api`, `hub-outbound-worker` (resource limits, non-root, HEALTHCHECK API, `stop_grace_period: 30s`, read-only root + tmpfs if practical)
- Test: `tests/unit/test_poison_taxonomy.py`; integration `tests/integration/test_outbound_flow.py`

**Interfaces:**
- Consume pending + retry.30s; group `hub-outbound-worker`
- Transition pending/retrying → delivering → HTTP
- Outbound headers spec §7.5; `X-Hub-Delivery-Id` = public_id; same HMAC construction
- httpx: connect/read from endpoint snapshot; **no** httpx retry transport
- 2xx → delivered + SLA helper; publish sla_breached if flag flips
- Transient → attempts+1; if < max: retrying, produce retry.30s (Stage 1 ignore full exponential delay except storing next_retry_at); else failed + DLQ topic + `dead_letters`
- Non-retryable → failed + DLQ immediately, commit offset
- Truncate response_body 4 KB
- SIGTERM: finish in-flight HTTP (max 30s)

**Spec:** §4.3, §4.7, §6.5, §7.5

**Acceptance:** Dockerfile USER non-root; no COPY .env; PYTHONDONTWRITEBYTECODE=1.

Poison taxonomy unit tests without Kafka.

---

### Task 13: Fault-injection integration subset (spec §10.3)

**Files:**
- Create: `tests/integration/test_fault_injection.py`, `tests/fixtures/partner_factory.py`, `tests/fixtures/kafka_helpers.py`
- Test: the table rows that Stage 1 can prove: 503×N then 200; 400→DLQ no retry; timeout→retry scheduled; duplicate inbound one Kafka message; manual replay after repair + audit. Skip CB/burst/outbox-catch-up with pytest.mark.stage2 if not built.

**Spec:** §10.3

**Acceptance:** Tests run via `make test-integration` against compose.test or testcontainers. If Docker missing: document BLOCKED and keep tests collected.

Do not combine httpx+asyncpg in one e2e process if SIGSEGV; use urllib for HTTP to Compose.

---

### Task 14: Thin admin UI (Vite + React + TS)

**Files:**
- Create: `admin_ui/package.json`, `admin_ui/src/pages/DeliveriesList.tsx`, `DeliveryDetail.tsx`, `DeadLetters.tsx`, `PartnersList.tsx`, `admin_ui/src/api/client.ts`, `admin_ui/Dockerfile` (multi-stage nginx:alpine non-root), `admin_ui/vite.config.ts`
- Modify: compose `hub-admin-ui` :8080

**Interfaces:**
- Only `/admin/v1`
- Replay button disabled until reason non-empty
- Exhaustive switch on delivery status
- Copy public id; payload truncated
- Error toasts from API `detail`

**Spec:** §14, ADR-006

**Acceptance:** No HMAC/retry logic in src. TypeScript exhaustive `never` default.

---

### Task 15: Seed + Grafana overview series note + inbound processor if needed

**Files:**
- Create: `scripts/seed_partners.py`, `scripts/seed_prod_like.py`
- Modify: Makefile `seed` / `seed-prod-like` (Kafka topics + partners); Grafana JSON queries using `partner_slug`
- Canonical slugs: `acme-erp` (ok), `flaky-logistics` (503 then 200), `strict-payments` (400), `slow-crm` (timeout)
- Event types: `order.created`, `order.updated`
- Prod-like: many partners (ERP, logistics, payments, CRM), mixed SLA, one `suspended`, one tight `sla_seconds`
- Do **not** seed all deliveries; traffic creates them (few historical optional)

**Spec:** prompt §2.3

**Acceptance:** Idempotent upserts. Admin bootstrap token usable after seed.

---

### Task 16: Contract tests, OpenAPI export, CI green, Stage 1 evidence file

**Files:**
- Create: `tests/contract/test_openapi_partner_mock.py`, `docs/asyncapi/asyncapi.yaml` (Stage 1 topics + envelope schema_version), `tests/contract/test_asyncapi_schemas.py`, local gitignored Stage 1 DoD evidence
- Modify: `scripts/generate_openapi.py` output `docs/openapi/openapi.yaml`
- Test: contract tests; `make export-openapi`; `make ci`

**Spec:** §7.3, §7.4, §10.2, prompt §15.2

**Acceptance:**
- Evidence checklist from prompt §15.2 filled with **commands and exit codes** (or explicit BLOCKED if Docker down)
- EN README: operator English; no gitignored-path links
- Do **not** write “Stage 1 Done”
- Then Orchestrator starts Stage 2 plan (separate file)

---

## Self-review (orchestrator)

- Every Stage 1 Must in spec §3.3 maps to a task above.
- No TBD / “similar to Task N” / commit steps.
- Circuit breaker, outbox relay, Celery beat, bulk replay, secret rotation table: Stage 2, not Acceptance here.
- Type names (`DeliveryStatus`, `sign`/`verify`, `create_app`) are stable for later tasks.
