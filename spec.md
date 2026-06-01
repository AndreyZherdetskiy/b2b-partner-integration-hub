# Technical requirements

## Partner Integration Hub

**Subtitle:** centralized B2B webhook delivery with retries, DLQ, audited replay, and partner SLA *measurement* (the hub does not compute contract penalties).

| Field | Value |
|-------|--------|
| Document version | **3.1 EN** |
| Date | 2026-06-02 |
| Changelog 3.1 | English product SoT; operator UI as §13; operator-neutral tone; stages 1–3 as delivered capabilities (human release commit pending); §7.2 lists `hub.outbound.retry.1m` with the other retry tiers |
| Changelog 3.0 | Prior revision; superseded by 3.1 EN |
| Status | Stages 1–3 capabilities in codebase (human release commit pending) |
| Implementation language | Python 3.12+ |
| Dependency manager | `uv` (preferred) or Poetry |
| Repository | `2_b2b_partner_integration_hub` |
| Product short name | Hub |
| Audience | Product owner, backend Mid+/Senior, DevOps/SRE, QA, solutions architect |

In this document the product is called the **Hub**.

---

## 0. Glossary (essentials)

Established English terms are introduced with a plain-language definition. After first introduction, accepted abbreviations are allowed: DLQ, HMAC, SLA, SLO, NFR, API, CI/CD, RBAC, JWT, ORM, SDK.

| Term | Definition |
|------|------------|
| Partner Integration Hub | Centralized platform for event exchange between a SaaS platform and external B2B partners; hereafter — **Hub** |
| Webhook | HTTP request carrying an event payload to a pre-registered URL |
| Outbound webhook | Delivery of a platform business event to a partner HTTP endpoint |
| Inbound webhook | Acceptance of an event from a partner at a Hub HTTP endpoint |
| Delivery | Unit of outbound send accounting (attempt chain with retries until a terminal outcome) |
| Delivery attempt | One HTTP attempt within a delivery |
| Retry | Another delivery attempt after a transient failure |
| Exponential backoff | Growing pause between retries with an upper bound |
| Jitter | Random deviation from the computed pause to avoid synchronized retry storms |
| Dead letter queue (DLQ) | Topic and/or table for deliveries that exhausted retries or hit a non-recoverable error |
| Poison message | Message that will not succeed for the partner with the current payload (typically 4xx) |
| Replay | Administrative or scheduled re-delivery of a failed / DLQ record |
| Idempotency | Safe retry: same key → no extra side effect |
| Idempotency-Key | Header/field for inbound and outbound deduplication |
| HMAC | Message authentication code; here HMAC-SHA256 over a shared secret |
| Secret rotation | Planned or emergency change of `signing_secret` / API keys with an overlap window |
| Circuit breaker | Protection: after a failure streak, outbound to a partner pauses (closed → open → half-open) |
| At-least-once | Message is delivered ≥1 time; duplicates are possible |
| Exactly-once | Single processing end-to-end; deliberately not promised by the Hub |
| Transactional outbox | DB write + outbox row in one transaction, then relay to Kafka without dual-write loss |
| Outbox relay | Background process: poll unpublished rows → Kafka → mark published |
| Dual-write | Anti-pattern: independent writes to DB and broker without a shared transaction |
| Event bus | Publish/consume channel (here Apache Kafka) |
| Topic | Named message stream in Kafka |
| Consumer group | Set of workers sharing topic offset progress |
| Offset | Consumer position in a topic partition |
| Consumer lag | Difference between latest topic offset and group offset |
| Contract test | Compatibility check of HTTP/AsyncAPI schemas and partner-mock expectations |
| AsyncAPI | Standard for describing Kafka events (OpenAPI analog for async) |
| OpenAPI | Standard for describing REST endpoints |
| RBAC | Role-based access control (`hub_admin`, `hub_operator`, …) |
| Multi-tenant | Logical data isolation by `partner_id` |
| SLA | Contract target for delivery time/quality with a partner |
| SLA compliance | Share of deliveries with first success within `sla_seconds` |
| SLO | Internal measurable goal (success rate, MTTR, etc.) |
| MTTR | Mean time from failure detection to successful delivery or DLQ escalation |
| Observability | Logs, metrics, tracing for delivery diagnosis |
| Dashboard | Grafana screen with integration-health metrics |
| Correlation id | End-to-end request id across API → worker → partner HTTP |
| UUIDv7 | Time-ordered UUID v7; convenient as PK/public id without a separate sequence |
| Dual-id / public_id | Internal `BIGINT` PK + stable `public_id` (UUIDv7) for API, Kafka, and replay; internal FKs use `BIGINT` |
| Composite PK | Multi-column PK; **not** used on tenant-like Hub tables |
| Natural UNIQUE | Business uniqueness without PK role: `slug`, `(partner_id, idempotency_key)`, `(delivery_id, attempt_number)` |
| Distributed tracing | Cross-service span correlation (OpenTelemetry — stage 3) |
| Readiness / liveness probe | Container health endpoints |
| Rate limiting | Per-partner request limit (Redis token bucket) |
| Retry storm | Mass concurrent retries against an unavailable partner |
| Sandbox | Test environment/endpoint for signature and contract checks |
| Schema registry | Payload JSON Schema versioning (stage 3) |
| Audit log / audit trail | Immutable history of sensitive actions (replay, rotation, DLQ purge) |
| Fault injection | Controlled reproduction of partner failures in tests and on the stand |
| ADR | Short record of a choice, alternatives, and trade-off |
| Definition of Done (DoD) | Stage/release acceptance checklist |
| NFR | Non-functional requirements (performance, security, availability, …) |
| Internal API | API for platform domain services |
| Admin API | API for SRE / support / integrators |
| Operator UI (admin UI) | Thin SPA over Admin API: deliveries, DLQ, replay |
| Runbook | Step-by-step response for DLQ growth, open circuit, etc. |
| CI/CD | Continuous integration / delivery pipeline |
| WAF / DDoS | Perimeter protection — outside Hub responsibility (infrastructure) |

---

## Contents

1. [Product goals and problem](#1-product-goals-and-problem)
2. [Personas, roles, and scenarios](#2-personas-roles-and-scenarios)
3. [Scope: in, out, and stages 1-3](#3-scope-in-out-and-stages-1-3)
4. [Architecture and invariants](#4-architecture-and-invariants)
5. [Technology stack](#5-technology-stack)
6. [Domain model and data](#6-domain-model-and-data)
7. [APIs and events](#7-apis-and-events)
8. [Non-functional requirements](#8-non-functional-requirements)
9. [Repository structure](#9-repository-structure)
10. [Testing and CI](#10-testing-and-ci)
11. [Definition of Done](#11-definition-of-done)
12. [ADR index summaries](#12-adr-index-summaries)
13. [Operator UI](#13-operator-ui)
14. [Appendices](#14-appendices)

---

## 1. Product goals and problem

### 1.1. Product summary

**Partner Integration Hub** (hereafter — **Hub**) is a centralized platform for event exchange between a SaaS platform and external B2B partners (marketplaces, ERP, logistics, payment aggregators, CRM, antifraud).

The Hub provides:

- **Outbound webhooks:** delivery of platform business events to partner URLs with **at-least-once** semantics, controlled retries, and full observability.
- **Inbound webhooks:** acceptance of partner events with **HMAC** verification, idempotency, and routing into internal Kafka topics.
- **Retry infrastructure:** **exponential backoff**, dedicated Kafka retry topics, attempt limit (`max_attempts`).
- **Dead letter queue (DLQ):** isolation of poison messages without blocking the main stream.
- **Audited replay:** administrative and scheduled replay of failures/DLQ via Admin API, thin operator UI, and scheduler tasks; every operation is recorded in the audit log.
- **Transactional outbox:** eliminates “silent” loss between writing a delivery in PostgreSQL and publishing to Kafka.
- **Partner SLA and compliance measurement:** target time to first successful delivery, breach metrics, alerts, and summaries for negotiations.
- **Observability:** Prometheus/Grafana dashboards, alerts on DLQ growth, structured logs, correlation.

The product removes **silent integration failures**: a webhook was “sent”, the partner never returned 2xx, and the business learns only via customer escalation or an **SLA** contract penalty.

### 1.2. Business context

A typical B2B SaaS with a partner ecosystem faces:

| Symptom | Consequence |
|---------|-------------|
| Webhook “sent” from the app, but HTTP timeout | Partner never got order/status; data divergence |
| Partner returned 500 three times | Event lost without DLQ or alert |
| Manual triage via SQL/scripts | MTTR 2–8 hours, duplicate risk |
| No unified delivery status | Support cannot answer “did the event arrive?” |
| Delivery-time SLA breach | Penalties 0.1–1% of turnover under contract; enterprise churn |
| No compliance measurement | Finance/Legal argue by feel; no facts for negotiation |
| Replay without audit | Cannot prove who replayed an event and why |

**Reference domain (reference scale):** services marketplace platform with 50–200 active B2B partners, 500k–2M outbound deliveries/day, 50–200k inbound requests/day.

### 1.3. Target business metrics (KPI / SLO)

| Metric | Baseline (before Hub) | Target (after stage 2) | Measurement |
|--------|----------------------|------------------------|-------------|
| Successful outbound share over 24 h (including retries) | 94–97% | ≥ 99.5% | `(delivered + successfully replayed) / accepted * 100` |
| Partner SLA compliance | 85–90% | ≥ 98% | Share with `first_success_at - created_at ≤ partners.sla_seconds` |
| Share of deliveries with a recorded SLA breach | Unmeasurable | 100% of breaches → event + metric | `hub_sla_breaches_total` without gaps |
| MTTR | 2–8 h (manual) | ≤ 15 min (auto-retry) + ≤ 30 min (manual UI replay) | P95 per incident |
| Share of silent terminal failures | Unmeasurable (~1–3% loss estimate) | 0% terminal failures without DLQ/alert | `failed` without `dead_letter` and without alert = 0 |
| Inbound idempotency collision handling | Duplicates → double orders | 100% dedup by `Idempotency-Key` | Contract tests + `hub_inbound_duplicate_suppressed_total` |
| L2 Support time to answer (“did webhook X arrive?”) | 30–60 min | ≤ 2 min via Admin API / UI | L2 UX metric |
| Share of replays with audit record | None | 100% | `audit_logs` for `delivery.replay` |

### 1.4. Partner SLA and compliance measurement

The Hub does not replace the legal SLA with a partner, but **operationalizes** it and provides measurable **SLA compliance**:

- Each partner (`partners.sla_seconds`) has a target time to first successful delivery (e.g. 60 s for `order.created`, 300 s for `inventory.sync`). Override at endpoint level is allowed (`partner_endpoints.sla_seconds`).
- The SLA clock stops at `first_success_at` (first successful HTTP 2xx), not at the last replay.
- On breach the Hub publishes `hub.integration.sla_breached` to Kafka, increments a metric, and surfaces the fact in Admin API / UI summary.
- Daily/weekly compliance aggregate per partner: share within SLA, top offending event types, DLQ backlog age.
- Monetary penalty calculation is **out of Hub scope**; only the breach fact and context for Finance/Legal are recorded.

Pain link: without compliance, “SLA penalties” are debated after the fact with no evidence; with the Hub there is a timestamp, attempts, and an alert before customer escalation.

### 1.5. Responsibility boundaries

| Hub is responsible for | Hub is NOT responsible for |
|------------------------|----------------------------|
| Accept, validate, route inbound webhooks | Business logic of handling the event in domain services |
| Queue, HTTP outbound delivery, retries | Correct payload interpretation on the partner side |
| DLQ, audited replay, delivery statuses | Legal format agreement with the partner |
| HMAC / API key at the boundary, secret rotation | Perimeter DDoS protection (WAF/CDN) — stage 3 / infrastructure |
| SLA/SLO metrics, logs, correlation, DLQ-growth alerts | Full partner self-service portal (thin admin UI exists) |
| Compliance measurement and breach facts | Assessing and collecting monetary penalties |

### 1.6. Key risks without the Hub

1. **Financial:** SLA penalties, compensation, manual reconciliation, disputes without facts.
2. **Reputational:** enterprise deals blocked by “how do you guarantee event delivery?”
3. **Operational:** on-call extinguishes integrations by hand; no shared **runbook**; replays without audit.
4. **Technical:** retry logic duplicated in every microservice; silent dual-write losses; divergent semantics.

### 1.7. Scale by stage (orientation)

| Stage | Horizon | Prep-stand scale |
|-------|---------|------------------|
| Stage 1 | 4–6 weeks | dozens of partners, hundreds RPS peak, one retry tier |
| Stage 2 | +6–8 weeks | full backoff, circuit breaker, outbox, compliance dashboards |
| Stage 3 | +8–10 weeks | up to 2M deliveries/day on the stand, multiple URLs per partner, schema registry |

### 1.8. Why a Hub (not ad-hoc HTTP per service)

Without a Hub, each domain service tends to invent its own outbound HTTP, retries, and logging — a silent dual-write and MTTR tax. The Hub is not “another FastAPI CRUD”: it is an event-driven delivery pipeline with retry taxonomy, DLQ workflow, an explicit delivery state machine, circuit breaker, transactional outbox, SLA compliance measurement, AsyncAPI contracts, fault-injection scenarios, and audited operator actions. Trade-offs are recorded in ADRs (why Kafka retry topics rather than Celery-only transport).

---

## 2. Personas, roles, and scenarios

### 2.1. Personas

#### P1 — Integration engineer (internal)

- **Goal:** onboard a new partner in 1–2 days without changing core code.
- **Pain:** each partner has its own retry script; no contract stand.
- **Access:** Admin API, admin UI, AsyncAPI/OpenAPI, sandbox.

#### P2 — Platform SRE / on-call

- **Goal:** quickly find the failure cause and safely replay.
- **Pain:** logs are scattered; no DLQ panel or backlog-growth alert.
- **Access:** Grafana, Prometheus alerts, Admin API/UI replay, read-only DB views.

#### P3 — L2 Support

- **Goal:** answer the customer “did `order.updated` reach partner X?”.
- **Pain:** no unified `delivery_id`.
- **Access:** limited Admin API/UI (read + replay; approval workflow — stage 3).

#### P4 — Partner developer (external)

- **Goal:** stably accept/send webhooks, verify signatures, survive secret rotation.
- **Pain:** unpredictable retries; no header documentation.
- **Access:** API keys, URL registration (via us), public AsyncAPI/OpenAPI, sandbox.

#### P5 — Product owner / BizDev

- **Goal:** see integration health and SLA compliance per partner for negotiations.
- **Pain:** no aggregated success share or SLA breach facts.
- **Access:** Grafana (read-only), compliance summary in admin UI, weekly report (stage 3).

#### P6 — Security / Compliance

- **Goal:** access audit, key rotation, proof of payload integrity and replays.
- **Pain:** secrets without rotation; no audit trail on replay.
- **Access:** audit logs, key rotation API (stage 2).

### 2.2. RBAC

| Role | Description | Rights |
|------|-------------|--------|
| `hub_admin` | Full access | CRUD partners/endpoints, replay, purge DLQ, rotate keys |
| `hub_operator` | SRE/Support | Read all, replay with limit, no partner delete |
| `hub_viewer` | PO/Analytics | Read metrics and deliveries, no replay |
| `partner_api` | Machine-to-machine | Inbound webhooks only for own `partner_id`; read own delivery status (stage 2) |
| `internal_service` | Domain services | Publish outbound events via Internal API / Kafka |

Admin API auth: JWT (internal IdP) or API key with scopes. Partner inbound: **API key** + **HMAC-SHA256** signature.

### 2.3. User scenarios

#### J1 — Onboard a new partner (outbound)

1. Engineer creates `partner` and `endpoint` (URL, secret, event types, `max_attempts`, backoff policy, `sla_seconds`).
2. Generate `api_key` (inbound) and `signing_secret` (HMAC in/out).
3. Test event via sandbox (`POST /admin/v1/deliveries/test`) or admin UI button.
4. Partner developer confirms receipt and correct `X-Hub-Signature-256`.
5. Endpoint enabled in production (`status=active`).
6. **Success:** test delivery `delivered`, contract test green, partner visible on compliance panel.

#### J2 — Outbound delivery with transient failure

1. Domain service publishes an event or calls Internal API.
2. Hub creates `deliveries` (`pending`) and an outbox row in one transaction; relay publishes to Kafka.
3. Worker HTTP POSTs via httpx; partner returns `503`.
4. Failure recorded; retry scheduled: publish to `hub.outbound.retry.{delay}` keyed by `delivery_id`.
5. After backoff — retry; partner returns `200`.
6. Delivery → `delivered`; metrics and SLA clock update on `first_success_at`.
7. **Success:** success rate includes the retry; compliance is counted correctly.

#### J3 — Exhausted retries → DLQ

1. After `max_attempts` (e.g. 8) status → `failed`.
2. Publish to `hub.outbound.dlq` with full context.
3. Insert `dead_letters` linked to `delivery_id`.
4. Alert: growth of `hub_dlq_messages_total` and/or backlog age (PagerDuty — stage 2).
5. SRE inspects via Admin API / UI.
6. **Success:** no terminal failure without a DLQ record and observability signal.

#### J4 — Administrative replay under audit

1. SRE finds `failed` or DLQ via `GET /admin/v1/deliveries?status=failed` or UI screen.
2. Reviews attempts and last response body.
3. Calls `POST /admin/v1/deliveries/{id}/replay` with `reason` and optional `reset_attempt_counter`.
4. New attempt / lineage: `replaying` → `delivered` / `failed`.
5. Audit log: `actor_id`, `reason`, timestamp, `delivery_id`, IP.
6. Rate limit: replays and partner outbound traffic respect the partner rate limit.
7. **Success:** replay is idempotent for the partner (same `Idempotency-Key`); 100% of operations audited.

#### J5 — Inbound webhook from partner

1. Partner POSTs to `https://hub.example.com/inbound/v1/{partner_slug}/events`.
2. Checks: `Authorization: Bearer <api_key>`, `X-Hub-Signature-256`, timestamp skew (±300 s).
3. `Idempotency-Key` in Redis + DB — on duplicate return `200` with original `event_id`.
4. Per-partner rate limit; on exceed — `429`.
5. Persist `inbound_events`, publish to `hub.inbound.{event_type}`.
6. Respond `202 Accepted` with `event_id`.
7. **Success:** duplicate does not create a second Kafka message.

#### J6 — Scheduled replay (Celery)

1. Celery Beat runs `replay_stale_failed_deliveries` (e.g. every 6 h).
2. Select `failed` older than 1 h where `auto_replay_enabled=true` and circuit is closed.
3. Batch replay with per-partner rate limit and audit (`trigger=scheduled`).
4. Metric `hub_scheduled_replay_total`.
5. **Success:** DLQ backlog shrinks without manual work when the partner recovers.

#### J7 — HMAC secret rotation

1. Security/admin calls `POST /admin/v1/partners/{id}/rotate-secret`.
2. New secret returned once; old remains valid in overlap window (e.g. 24 h).
3. Outbound signed with new secret; inbound accepts both in the window.
4. Audit: `api_key.rotate` / `signing_secret.rotate`.
5. **Success:** no integration downtime; old secret revoked after the window.

#### J8 — SLA compliance overview for negotiations

1. PO opens partner summary in UI or Grafana.
2. Sees compliance share for the period, `sla_breached` count, top problematic `event_type`.
3. Exports facts for Finance/Legal (stage 3 — report; stage 2 — analytics API).
4. **Success:** penalty disputes rest on Hub measurements, not ticket threads.

---

## 3. Scope: in, out, and stages 1-3

### 3.1. In scope (cumulative by stage)

- Centralized registry of partners and webhook endpoints.
- Outbound: accept events, HTTP delivery, exponential backoff, max attempts.
- Inbound: accept, HMAC + API key, idempotency, per-partner rate limit.
- Kafka: main topics, retry topics, DLQ.
- PostgreSQL: durable state (deliveries, attempts, dead letters, audit, transactional **outbox**).
- Redis: idempotency cache, rate limits, circuit-breaker state, Celery broker, distributed locks.
- **Transactional outbox** + publish relay (required from stage 2; stage 1 — simplified path with discrepancy metric, deliberately).
- Admin API: CRUD, delivery query, audited replay, DLQ management, secret rotation.
- Thin admin UI: deliveries, DLQ, replay — no business logic on the client.
- OpenAPI (REST) + AsyncAPI (Kafka events).
- Partner SLA and compliance measurement (fields, metrics, alerts, summaries).
- Prometheus + Grafana (health, SLA, DLQ) + DLQ-growth alerts.
- Docker Compose for local env and prep stand.
- pytest: unit, integration, **contract tests**, **fault-injection** scenarios.
- GitHub Actions: lint (Ruff), typecheck (mypy), tests, contract validation.
- structlog JSON with `correlation_id`, `delivery_id`, `partner_id`.

### 3.2. Out of scope

- Full Partner Portal (partner self-service) — API + thin internal admin UI only.
- Payload transform/mapping across schema versions — JSON Schema / Pydantic validation only; mapping as plugin — stage 3.
- SOAP, FTP, EDI — HTTP webhooks only.
- Multi-region active-active — stage 3+ / separate hardening.
- Billing partners for Hub usage.
- WAF, DDoS, mTLS at the perimeter — documented as infrastructure zone.
- End-to-end exactly-once guarantee (only at-least-once + consumer/partner idempotency).
- Legal calculation and collection of monetary SLA penalties.

### 3.3. Stage 1 — MVP (4–6 weeks)

**Goal:** end-to-end outbound + inbound + one retry tier + DLQ + manual audited replay + thin delivery-list UI + basic SLA metrics.

| Component | Details |
|-----------|---------|
| Partners & endpoints | CRUD, `signing_secret`, `api_key`, `sla_seconds`, one outbound URL per partner |
| Outbound flow | Kafka `hub.outbound.pending` → HTTP worker → attempts journal |
| Retries | One topic `hub.outbound.retry.30s` (fixed 30 s); full exponential — stage 2 |
| DLQ | Topic `hub.outbound.dlq` + table `dead_letters` |
| Inbound | HMAC, idempotency key, publish to `hub.inbound.{type}` |
| Admin API | List/get deliveries, single-delivery replay with `reason` → `audit_logs` |
| Admin UI | Delivery list, detail with attempts, replay button, DLQ list |
| Auth | API keys (partners), static admin token → JWT stub |
| Observability | 10–15 key Prometheus metrics, 1 Grafana dashboard, DLQ-growth alert |
| SLA | Field `sla_seconds`, metric `hub_sla_breaches_total` (simplified) |
| Outbox | Deliberately simplified: publish after commit + discrepancy metric; full **outbox** — stage 2 |
| Tests | ≥ 80% core coverage; contract tests for 2 event types; basic fault injection |
| Deployment | Docker Compose (`hub-api`, worker, kafka, postgres, redis, prometheus, grafana, admin-ui) |

**Stage 1 KPI:** success rate ≥ 99% on fault-injection stand; replay works; DLQ 100% on exhausted retries; no terminal failure without record/alert.

### 3.4. Stage 2 — Production shape (6–8 weeks)

**Goal:** full backoff, circuit breaker, **transactional outbox**, richer Admin API/UI, Celery maintenance, security hardening, compliance dashboards.

| Component | Details |
|-----------|---------|
| Retry topics | `hub.outbound.retry.1m`, `.5m`, `.15m`, `.1h` (policy per **endpoint**) |
| Exponential backoff | `delay = min(base * 2^attempt, max_delay)` with jitter ±10% |
| Circuit breaker | Per-partner in Redis: open after N failures in window |
| Outbox | Table `outbox_events` + relay `outbox_relay`; eliminate dual-write |
| httpx | Connect/read timeouts, connection pool; POST retry only via Kafka |
| Celery | `replay_stale_failed`, `purge_old_idempotency_keys`, `rotate_webhook_secrets` (notify) |
| Rate limiting | Inbound/outbound and bulk-replay: token bucket per partner |
| Admin API | Bulk replay, DLQ list/ack/purge, compliance analytics, secret rotation with overlap |
| Admin UI | Filters, bulk replay, ack DLQ, partner summary (success rate, SLA breaches, circuit) |
| Delivery state machine | Full transition graph (§6.5) with validation |
| Security | API key / signing_secret rotation, full audit log, timestamp anti-replay |
| Grafana dashboards | SLA compliance, success rate by partner, MTTR, DLQ age, DLQ growth |
| Alerts | DLQ growth rate, unacked age, circuit open, compliance drop |
| Contract tests | AsyncAPI validation in CI; Pact-style partner mock; fault-injection suite |

### 3.5. Stage 3 — Enterprise and scale (8–10 weeks)

**Goal:** multiple endpoints per partner, schema registry, replay approval, optional read replica for Admin list, load.

| Component | Details |
|-----------|---------|
| Multiple URLs | Multiple endpoints per partner with routing by `event_type` |
| Schema registry | Payload versioning (JSON Schema in PG or Confluent-compatible stub) |
| Replay approval | Support → pending approval → `hub_admin` confirms |
| Partner status API | `GET /partner/v1/deliveries/{id}` read-only |
| HA Kafka | 3 brokers in compose/k8s docs; documented consumer groups — **prod RF=3 in docs**; local Compose stays one broker RF=1 |
| Performance | Target 2M deliveries/day on the stand (k6/Locust) |
| Read replica (optional) | RO PostgreSQL replica for Admin delivery/DLQ lists; delivery and outbox writes — primary only |
| Reporting | Weekly SLA compliance export for BizDev/Finance |
| Documentation | Response runbooks, ADR pack, EN README for the public repository |
| Tracing | OpenTelemetry (`correlation_id` → `trace_id`); traces to **Jaeger** (not Tempo) |

### 3.6. Post-MVP (not blocking DoD)

- OpenTelemetry traces (`correlation_id` → `trace_id`).
- Kubernetes Helm chart.
- Pact consumer-driven contracts with real partners.
- Payload encryption (JWE) for regulated data.
- Automatic weekly SLA compliance report for BizDev.

---

## 4. Architecture and invariants

### 4.1. System context (C4 Level 1)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Partner Integration Hub (B2B)                           │
│  ┌──────────────┐    webhooks in/out       ┌──────────────────────────────┐ │
│  │ Domain       │◄────────────────────────►│ External B2B partners          │ │
│  │ services     │   (via outbound)         │ (ERP, marketplaces, logistics)│ │
│  │ (Orders,     │                          └──────────────────────────────┘ │
│  │  Billing…)   │                                                           │
│  └──────┬───────┘                                                           │
│         │ publish/consume                                                   │
│         ▼                                                                   │
│  ┌──────────────┐         ┌─────────────┐         ┌─────────────────────┐  │
│  │ Apache Kafka │◄───────►│ Hub core    │────────►│ PostgreSQL + Redis  │  │
│  └──────────────┘         │ (FastAPI +  │         └─────────────────────┘  │
│                           │  Workers +  │                                     │
│  ┌──────────────┐         │  Outbox     │         ┌─────────────────────┐  │
│  │ Admin / SRE  │◄────────│  Relay)     │────────►│ Prometheus/Grafana  │  │
│  │ API + UI     │         └─────────────┘         └─────────────────────┘  │
│  └──────────────┘                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Actors:**

- **Domain services** — initiators of outbound events.
- **External partners** — HTTP endpoints (outbound) and inbound sources.
- **Admin users** — engineers, SRE, support via Admin API and thin UI.
- **Celery Beat/Workers** — scheduled maintenance (replay, cleanup, rotation).
- **Outbox relay** — Kafka publish without dual-write loss.

### 4.2. Containers (C4 Level 2)

| Container | Technology | Responsibility |
|-----------|------------|----------------|
| `hub-api` | FastAPI | Inbound HTTP, Admin REST, Internal API, health, `/metrics` |
| `hub-admin-ui` | Vite + React + TypeScript SPA | Thin deliveries/DLQ/replay UI; all logic on backend |
| `hub-outbound-worker` | Python async consumer | Consume `pending`/`retry` → HTTP delivery |
| `hub-inbound-processor` | Kafka consumer (may share **worker**) | Validate, persist, route to internal topics |
| `hub-outbox-relay` | Python **worker** | Relay `outbox_events` → Kafka (stage 2) |
| `hub-scheduler` | Celery + Redis broker | Scheduled replay, maintenance, rotation |
| `postgresql` | PostgreSQL 16.15 | Source of truth: partners, deliveries, attempts, DLQ, audit, outbox |
| `redis` | Redis 8 | Idempotency, circuit breaker, Celery broker, rate limits |
| `kafka` | Apache Kafka 4.3.x (KRaft) | Event bus, retry topics, DLQ |
| `prometheus` / `grafana` | — | Metrics, dashboards, alerts |
| `partner-mock` | WireMock / FastAPI | Contract tests and local development (not prod) |

### 4.3. Outbound delivery pipeline (with **outbox**)

```
Domain service
    │ 1. POST /internal/v1/outbound/events  OR  publish hub.outbound.accepted
    ▼
hub-api / ingress
    │ 2. Schema validation, resolve partner+endpoint
    │ 3. In ONE DB transaction:
    │       create delivery (pending)
    │       insert outbox_events (stage 2)
    │ 4. outbox-relay → publish hub.outbound.pending
    ▼
hub-outbound-worker
    │ 5. Check circuit breaker (Redis) and partner rate limit
    │ 6. HTTP POST httpx → partner URL
    │    Headers: X-Hub-Delivery-Id, X-Hub-Signature-256,
    │    Idempotency-Key, X-Hub-Timestamp
    ├── success (2xx) → delivery.status=delivered; SLA clock stop
    └── failure (non-2xx, timeout, network)
            │ 7. Insert delivery_attempts
            │ 8. if attempt < max_attempts AND retryable:
            │       compute backoff → publish hub.outbound.retry.{tier}
            │    else:
            │       delivery.status=failed
            │       publish hub.outbound.dlq
            │       insert dead_letters
            └── alert (DLQ growth / SLA breach)
```

### 4.4. Inbound webhook pipeline

```
Partner HTTP POST /inbound/v1/{slug}/events
    │ 1. Rate limit (token bucket per partner)
    │ 2. Authenticate API key
    │ 3. Verify HMAC-SHA256(secret, timestamp + "." + body)
    │    (during rotation — check primary and previous secret)
    │ 4. Idempotency-Key in Redis (TTL 24h) + UNIQUE in DB
    │ 5. Persist inbound_events
    │ 6. Publish hub.inbound.{event_type}
    ▼
Domain consumers (outside Hub)
```

### 4.5. Why Kafka, not Celery/Redis Queue alone

| Criterion | Kafka | Celery/Redis only |
|-----------|-------|-------------------|
| Retry topics as first-class | Separate topics with retention | Harder to emulate delay tiers |
| Scale 2M+/day | Proven throughput | Redis lists bottleneck |
| Audit / triage log | Retained log, offsets | No built-in journal |
| DLQ pattern | Dedicated topic + consumer | Celery dead letter is limited |
| Contract testing (AsyncAPI) | Natural model | Less aligned with event-first documentation |

**Celery role:** not primary delivery transport, but **scheduled maintenance** (maintenance replay, cleanup, rotation notifications) — familiar stack from commercial practice; does not duplicate Kafka semantics. Kafka retries are not Celery countdown.

### 4.6. Retry topics vs Redis delayed queue

Topics `hub.outbound.retry.1m` and similar give:

- delay isolation (1h messages do not block 30s);
- lag observability per tier;
- poison isolation: after max attempts — only DLQ; `pending` is not polluted.

### 4.7. Poison messages

**Definition:** a message that will **never** be successfully processed by the partner with the current payload (e.g. 400/422).

| HTTP class | Behavior |
|------------|----------|
| 408, 429, 5xx, network | Retry with backoff |
| 400, 401, 403, 404, 422 | **No retry** (non-retryable) → immediate `failed` + DLQ |
| 2xx | Success |

Config `endpoints.retry_on_status_codes` per partner (default — transient only).

After writing to DLQ the consumer commits the offset and continues — the partition is not blocked. This reduces silent partition stalls and SLA-penalty accumulation from blocking unrelated deliveries.

### 4.8. Circuit breaker per partner

- **Closed:** normal delivery.
- **Open:** after `failure_threshold` (e.g. 10 failures / 60 s) — pause outbound for `open_duration` (e.g. 5 min).
- **Half-open:** probe delivery; success → closed, failure → open.

State in Redis: `circuit:{partner_id}` with TTL. Protects against **retry storm** and preserves SLA budget.

If Redis is down: **fail-open** (continue delivery) with DB rate-limit fallback — document the choice (see ADR-005).

### 4.9. Idempotency

- **Outbound:** header `Idempotency-Key: {delivery_id}` or `{source_event_id}` — partner must deduplicate (OpenAPI contract).
- **Inbound:** unique index `(partner_id, idempotency_key)` + Redis fast path.
- **Replay:** same `Idempotency-Key` — partner must return the same 2xx without duplicate side-effect.

### 4.10. Transactional outbox

**Pain:** `deliveries` write in PostgreSQL succeeded, Kafka publish failed → API accepted the event but the worker never sees it = classic silent failure and SLA-penalty risk.

**Solution (stage 2):** in the same transaction as delivery creation, insert `outbox_events`; `hub-outbox-relay` publishes and sets `published_at`. Relay idempotency by `outbox_id` / `delivery_id`.

**Stage 1:** simplified path allowed deliberately, with discrepancy metric and manual replay; stage 2 DoD requires the transactional outbox.

### 4.11. Per-partner rate limiting

- **Inbound:** Redis token bucket (default 100 rps/partner) → `429` + metric.
- **Outbound:** concurrency/RPS cap per partner so we do not overwhelm their endpoint or accelerate circuit open.
- **Bulk replay:** stricter separate limit + mandatory `reason` in audit.

Pain link: without rate limit, partner recovery after outage becomes a second storm and a new wave of SLA breaches.

### 4.12. ADR summary table

| ADR | Decision | Alternative | Why chosen |
|-----|----------|-------------|------------|
| ADR-001 | At-least-once + idempotency | Exactly-once via Kafka transactions | Simpler and sufficient for webhooks |
| ADR-002 | Kafka retry topics | Single delay queue | Observability + tier isolation |
| ADR-003 | PostgreSQL as source of truth | Event sourcing only | Convenient Admin API/UI queries |
| ADR-004 | HMAC-SHA256 | mTLS only | Webhook standard (Stripe-style) |
| ADR-005 | httpx + circuit breaker in Redis | requests + manual retry | Async, timeouts, testability |
| ADR-006 | Thin admin UI over Admin API | curl/Grafana only | Speeds demo and Support/SRE MTTR |
| ADR-007 | **Transactional outbox** | Sync publish after commit | Eliminates silent dual-write loss |
| ADR-008 | SLA compliance measurement in Hub | External BI only | Operationalize penalties and alerts |
| ADR-009 | UUIDv7 + dual-id only for partners/deliveries | UUID v4 everywhere; composite PK | See §6.3 and §12.9 |
| ADR-010 | Multi-URL `event_type` fan-out; stored key `{client_key}::{endpoint.public_id}` | Single endpoint `.limit(1)`; change UNIQUE to include endpoint | Stage 3: all matching active outbound URLs; UNIQUE `(partner_id, idempotency_key)` unchanged |

Expanded rationale — [§12](#12-adr-index-summaries).

---

## 5. Technology stack

| Technology | Version | Role |
|------------|---------|------|
| Python | 3.12+ | Primary language |
| FastAPI | 0.141.x (`>=0.141,<0.142`) | HTTP API (inbound, admin, internal) |
| Uvicorn | 0.52.x | ASGI server |
| Pydantic | v2.8+ / 2.13.x (`<3`) | Request/response models, settings, event schemas |
| SQLAlchemy | 2.0.x (async) | ORM, async sessions (`asyncpg`) |
| asyncpg | 0.31.x | PostgreSQL driver |
| Alembic | `>=1.13,<2` | DB schema migrations (schema SoT) |
| PostgreSQL | 16.15 (16.x line) | Persistent store |
| Redis | 8.x (Compose `redis:8.10`; redis-py 8.1) | Cache, Celery broker, circuit breaker, rate limit |
| Apache Kafka | 4.3.x (KRaft in Compose; `apache/kafka:4.3.1`) | Event bus, retries, DLQ |
| aiokafka | 0.14.x | Consumers/producers (**aiokafka** for async workers) |
| httpx | 0.27+ | Async HTTP client to partners |
| tenacity / custom | — | Limited; primary retry via Kafka |
| pybreaker or custom Redis CB | — | Circuit breaker per partner |
| Celery | 5.6.x (maintenance only; Redis via `redis[hiredis]`) | Scheduled tasks (replay, cleanup, rotation) |
| structlog | 26.x | JSON structured logging |
| cryptography | 50.x | Fernet encryption for signing secrets at rest |
| OpenTelemetry API/SDK | 1.44 | Traces and metrics export (OTLP) |
| OpenTelemetry instrumentation | 0.65b0 | FastAPI, httpx, ASGI auto-instrumentation |
| prometheus-client | 0.20+ | Metrics |
| OpenAPI | 3.1 | REST docs (auto from FastAPI) |
| AsyncAPI | 3.0 | Kafka event docs |
| React + Vite + TypeScript | current LTS | Thin admin UI |
| Docker / Docker Compose | 24+ / v2 | Local env and prep stand |
| pytest | 8+ | Tests |
| pytest-asyncio | 0.23+ | Async tests |
| httpx / respx | — | HTTP mock |
| testcontainers-python | 4+ | Kafka, PG, Redis in integration tests |
| Ruff | 0.5+ | Lint + format |
| mypy | 1.10+ | Static typing (strict on `app/`) |
| GitHub Actions | — | CI/CD |
| uv (or Poetry) | — | Dependencies (**uv** preferred for CI speed) |

**Deliberately deferred:** OpenTelemetry traces beyond stage 3 baseline, Kubernetes manifests (stage 3), Confluent Schema Registry (stage 3). Local Compose: **one** Kafka broker, RF=1; production RF=3 is docs-only.

---
## 6. Domain model and data

### 6.1. Bounded contexts

1. **Partner registry** — partners, endpoints, credentials, SLA config.
2. **Delivery execution** — deliveries, delivery_attempts, state machine.
3. **DLQ management** — dead_letters, replay lineage.
4. **Inbound ingress** — inbound_events.
5. **Reliable publish** — outbox_events + relay.
6. **Audit and compliance** — audit_logs, SLA aggregates (stage 2+).

### 6.2. Logical ER diagram

```
partners 1───* partner_endpoints
partners 1───* partner_api_keys
partners 1───* partner_signing_secrets   (rotation history)
partner_endpoints 1───* deliveries
deliveries 1───* delivery_attempts
deliveries 0───1 dead_letters
deliveries 0───* outbox_events
partners 1───* inbound_events
* ───* audit_logs (polymorphic resource reference)
```

### 6.3. Identifier and key policy

| Entity | PK | Public ID | Note |
|--------|-----|-----------|------|
| `partners`, `deliveries` | `BIGINT` (dual-id) | `public_id` UUIDv7 UNIQUE | API, Kafka, replay — **only** `public_id`; internal FKs to dual-id tables — by `BIGINT` |
| `partner_endpoints`, `delivery_attempts`, `dead_letters`, `inbound_events` | UUIDv7 | = PK | External contract = PK |
| `outbox_events` | `BIGINT` | — | Sequential append-only journal; not exposed externally |
| Others (`partner_signing_secrets`, `partner_api_keys`, `audit_logs`) | UUIDv7 | = PK | Satellite / audit |

**Rules:**

1. **No composite PK.** Tenant-like uniqueness is `UNIQUE`, not PK.
2. **Natural UNIQUE (not PK):** `(partner_id, idempotency_key)` on `deliveries` and `inbound_events`; `(delivery_id, attempt_number)` on `delivery_attempts`; `partners.slug`.
3. **FK:** where target is dual-id (`partners`, `deliveries`) — FK column type `BIGINT`; where target has UUIDv7 PK — FK UUIDv7.
4. **Do not confuse** internal dual-id `id` with `public_id` in OpenAPI/Admin UI/replay runbooks.

Dual-id boundary: **`partners` + `deliveries` only**.

### 6.4. Tables and key fields

#### `partners`

| Field | Type | Description |
|-------|------|-------------|
| `id` | BIGINT PK | Internal dual-id |
| `public_id` | UUIDv7 UNIQUE | API / Kafka / replay |
| `slug` | VARCHAR(64) UNIQUE | Natural UNIQUE, URL-safe (`acme-erp`) |
| `name` | VARCHAR(255) | Display name |
| `status` | ENUM | `active`, `suspended`, `provisioning` |
| `sla_seconds` | INTEGER | Target SLA to first successful delivery |
| `auto_replay_enabled` | BOOLEAN | Allow scheduled Celery replay |
| `circuit_breaker_config` | JSONB | `{failure_threshold, window_seconds, open_duration}` |
| `rate_limit_rps` | INTEGER | Inbound/outbound limit (default 100) |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Canonical demo slugs include `acme-erp`, `flaky-logistics`, `strict-payments`, `slow-crm`.

#### `partner_signing_secrets`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUIDv7 PK | |
| `partner_id` | BIGINT FK | → `partners.id` |
| `secret_encrypted` | BYTEA | HMAC secret (encryption at rest) |
| `version` | INTEGER | Rotation version |
| `status` | ENUM | `primary`, `previous`, `revoked` |
| `valid_from`, `valid_until` | TIMESTAMPTZ | Overlap window |
| `created_at` | TIMESTAMPTZ | |

At stage 1 a single `signing_secret` field on `partners` is allowed, with migration to history table at stage 2.

#### `partner_api_keys`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUIDv7 PK | |
| `partner_id` | BIGINT FK | → `partners.id` |
| `key_prefix` | VARCHAR(16) | Prefix for identification (`pk_live_abc…`) |
| `key_hash` | VARCHAR(255) | bcrypt/argon2 hash of full key |
| `scopes` | TEXT[] | `inbound:write`, `status:read` |
| `expires_at` | TIMESTAMPTZ NULL | |
| `revoked_at` | TIMESTAMPTZ NULL | |
| `created_at` | TIMESTAMPTZ | |

#### `partner_endpoints`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUIDv7 PK | |
| `partner_id` | BIGINT FK | → `partners.id` |
| `direction` | ENUM | `outbound`, `inbound` |
| `url` | TEXT | HTTPS URL (outbound) |
| `event_types` | TEXT[] | Subscription: `order.created`, `order.updated`, … |
| `status` | ENUM | `active`, `paused`, `disabled` |
| `sla_seconds` | INTEGER NULL | Partner SLA override |
| `max_attempts` | SMALLINT | Default 8 |
| `backoff_policy` | JSONB | `{base_seconds, multiplier, max_seconds, jitter_pct}` |
| `retry_on_status_codes` | INTEGER[] | Default transient codes |
| `timeout_connect_ms` | INTEGER | Default 3000 |
| `timeout_read_ms` | INTEGER | Default 10000 |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `(partner_id, status)`, GIN on `event_types`.

#### `deliveries`

| Field | Type | Description |
|-------|------|-------------|
| `id` | BIGINT PK | Internal dual-id |
| `public_id` | UUIDv7 UNIQUE | API / Kafka / replay (external `delivery_id`) |
| `partner_id` | BIGINT FK | → `partners.id` |
| `endpoint_id` | UUIDv7 FK | → `partner_endpoints.id` |
| `direction` | ENUM | `outbound` (inbound — separate table) |
| `event_type` | VARCHAR(128) | |
| `idempotency_key` | VARCHAR(255) | Natural UNIQUE with `partner_id` |
| `payload` | JSONB | Webhook body |
| `payload_hash` | VARCHAR(64) | SHA-256 for dedup/audit |
| `status` | ENUM | See §6.5 |
| `attempt_count` | SMALLINT | Current attempt count |
| `max_attempts` | SMALLINT | Snapshot at creation |
| `next_retry_at` | TIMESTAMPTZ NULL | |
| `first_success_at` | TIMESTAMPTZ NULL | For SLA / compliance |
| `sla_deadline_at` | TIMESTAMPTZ | `created_at + sla_seconds` (snapshot) |
| `sla_breached` | BOOLEAN | Breach fact (set once) |
| `last_error_code` | VARCHAR(64) NULL | |
| `last_error_message` | TEXT NULL | |
| `correlation_id` | VARCHAR(128) | End-to-end trace |
| `source_event_id` | VARCHAR(255) NULL | ID from domain system |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

UNIQUE: `(partner_id, idempotency_key)` — not PK.

For multi-URL fan-out (stage 3 / ADR-010): stored `idempotency_key = {client_key}::{endpoint.public_id}`; caller key kept as `source_event_id`.

Indexes: `(status, next_retry_at)`, `(partner_id, created_at DESC)`, `(correlation_id)`, `(partner_id, sla_breached, created_at)`, `(public_id)`.

#### `delivery_attempts`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUIDv7 PK | |
| `delivery_id` | BIGINT FK | → `deliveries.id` |
| `attempt_number` | SMALLINT | 1..N |
| `requested_at` | TIMESTAMPTZ | |
| `responded_at` | TIMESTAMPTZ NULL | |
| `http_status_code` | INTEGER NULL | |
| `response_headers` | JSONB | Truncated |
| `response_body` | TEXT | Truncated, max 4 KB |
| `error_type` | VARCHAR(64) | `timeout`, `connection`, `http_error`, `circuit_open`, `rate_limited` |
| `duration_ms` | INTEGER | |
| `created_at` | TIMESTAMPTZ | |

UNIQUE: `(delivery_id, attempt_number)` — not PK.

#### `dead_letters`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUIDv7 PK | |
| `delivery_id` | BIGINT FK UNIQUE | → `deliveries.id` |
| `partner_id` | BIGINT FK | → `partners.id` |
| `reason` | ENUM | `max_attempts_exceeded`, `non_retryable_error`, `manual_purge` |
| `last_http_status` | INTEGER NULL | |
| `last_error_message` | TEXT | |
| `kafka_offset` | BIGINT NULL | Reference for log triage |
| `acknowledged_at` | TIMESTAMPTZ NULL | SRE ack |
| `acknowledged_by` | VARCHAR(255) NULL | |
| `created_at` | TIMESTAMPTZ | |

#### `inbound_events`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUIDv7 PK | `event_id` |
| `partner_id` | BIGINT FK | → `partners.id` |
| `idempotency_key` | VARCHAR(255) | UNIQUE with `partner_id` (not PK) |
| `event_type` | VARCHAR(128) | |
| `payload` | JSONB | |
| `payload_hash` | VARCHAR(64) | |
| `signature_valid` | BOOLEAN | |
| `signing_secret_version` | INTEGER NULL | Which secret accepted the signature |
| `received_at` | TIMESTAMPTZ | |
| `published_at` | TIMESTAMPTZ NULL | To Kafka |
| `correlation_id` | VARCHAR(128) | |

#### `outbox_events` (stage 2, required)

| Field | Type | Description |
|-------|------|-------------|
| `id` | BIGINT PK | Sequential append-only |
| `aggregate_type` | VARCHAR(64) | `delivery` |
| `aggregate_id` | BIGINT | Internal `deliveries.id` |
| `topic` | VARCHAR(128) | Target Kafka topic |
| `payload` | JSONB | Event envelope |
| `created_at` | TIMESTAMPTZ | |
| `published_at` | TIMESTAMPTZ NULL | NULL = not yet published |
| `publish_attempts` | SMALLINT | |

Index: `(published_at NULLS FIRST, created_at)` for the relay.

#### `audit_logs` (stage 1: replay; stage 2: full)

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUIDv7 PK | |
| `actor_id` | VARCHAR(255) | |
| `action` | VARCHAR(64) | `delivery.replay`, `dlq.ack`, `signing_secret.rotate`, `api_key.rotate` |
| `resource_type` | VARCHAR(64) | |
| `resource_id` | UUIDv7 | Public resource id (`public_id` / UUIDv7 PK) |
| `metadata` | JSONB | reason, ip, user_agent, reset_attempt_counter, trigger |
| `created_at` | TIMESTAMPTZ | |

### 6.5. Delivery state machine (outbound)

```
                    ┌─────────────┐
                    │   pending   │◄── create delivery
                    └──────┬──────┘
                           │ worker picks up
                           ▼
                    ┌─────────────┐
               ┌───►│  delivering │───┐
               │    └─────────────┘   │
               │           │          │
               │     2xx   │   fail   │
               │           ▼          │
               │    ┌─────────────┐   │
               │    │  delivered  │   │ (terminal success)
               │    └─────────────┘   │
               │                      │
               │    retries left      │ no retries / non-retryable
               │           ▼          ▼
               │    ┌─────────────┐  ┌─────────────┐
               └─── │   retrying  │  │   failed    │──► dead_letters
                    └─────────────┘  └──────┬──────┘
                           │                │
                           │                │ admin / Celery replay
                           │                ▼
                           │         ┌─────────────┐
                           └────────►│  replaying  │──► delivered | failed
                                     └─────────────┘
```

**Transition rules:**

| From | To | Trigger |
|------|----|---------|
| `pending` | `delivering` | Worker picked up message |
| `delivering` | `delivered` | HTTP 2xx |
| `delivering` | `retrying` | Transient error, attempts < max |
| `delivering` | `failed` | Non-retryable OR attempts ≥ max |
| `retrying` | `delivering` | Message from retry topic consumed |
| `failed` | `replaying` | Admin/Celery replay (+ audit) |
| `replaying` | `delivered` / `failed` | Same as delivering |

Invalid transition → log + metric `hub_invalid_transition_total` (bug).

On transition to `delivered`: if `first_success_at` empty — set it; if `now > sla_deadline_at` and `sla_breached=false` — set breach + event/metric.

### 6.6. Exponential backoff (formula)

```
delay_seconds = min(base_seconds * (multiplier ^ (attempt_number - 1)), max_seconds)
jitter = delay_seconds * random.uniform(-jitter_pct, jitter_pct)
scheduled_at = now() + delay_seconds + jitter
```

**Default policy:** `base=30`, `multiplier=2`, `max=3600`, `jitter_pct=0.1`.

**Mapping to retry topics (stage 2):**

| attempt | delay tier | topic |
|---------|------------|-------|
| 2 | ~30 s | `hub.outbound.retry.30s` |
| 3–4 | ~1–5 min | `hub.outbound.retry.5m` |
| 5–6 | ~15 min | `hub.outbound.retry.15m` |
| 7–8 | ~1 h | `hub.outbound.retry.1h` |

---

## 7. APIs and events

### 7.1. REST API — endpoint groups

**Base URLs:**

- Public inbound: `https://hub.example.com/inbound/v1`
- Admin: `https://hub.example.com/admin/v1`
- Internal: `https://hub.internal/v1` (network policy)

#### 7.1.1. Inbound API (partner)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/inbound/v1/{partner_slug}/events` | Accept webhook |
| GET | `/inbound/v1/health` | Health (no auth) |

**Required headers:**

- `Authorization: Bearer <api_key>`
- `X-Hub-Signature-256: sha256=<hex>` — HMAC of `{timestamp}.{raw_body}`
- `X-Hub-Timestamp: <unix_seconds>`
- `Idempotency-Key: <string>`
- `Content-Type: application/json`

**HMAC verification algorithm:**

1. Read raw body bytes.
2. Check `|now - timestamp| ≤ 300` seconds (anti-replay).
3. `signed_payload = f"{timestamp}.".encode() + body`
4. Compute expected signature for `primary` (and `previous` if needed) secret.
5. Constant-time compare with header signature.

HMAC formula: `timestamp + "." + raw body`.

**Responses:**

- `202 Accepted` — `{ "event_id": "uuid", "status": "accepted" }`
- `200 OK` — idempotency duplicate (same `event_id`)
- `401 Unauthorized` — bad API key
- `403 Forbidden` — bad signature / expired timestamp
- `422 Unprocessable Entity` — schema validation error
- `429 Too Many Requests` — partner rate limit exceeded

#### 7.1.2. Admin API — partners and endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/admin/v1/partners` | hub_admin | Create partner (incl. `sla_seconds`) |
| GET | `/admin/v1/partners` | hub_viewer+ | List with filters |
| GET | `/admin/v1/partners/{id}` | hub_viewer+ | Details |
| PATCH | `/admin/v1/partners/{id}` | hub_admin | Update SLA, status, rate limit |
| POST | `/admin/v1/partners/{id}/api-keys` | hub_admin | Create API key (plain — once) |
| POST | `/admin/v1/partners/{id}/rotate-secret` | hub_admin | Rotate `signing_secret` with overlap |
| POST | `/admin/v1/partners/{id}/endpoints` | hub_admin | Create endpoint |
| PATCH | `/admin/v1/endpoints/{id}` | hub_admin | Pause/resume, URL, SLA override |
| GET | `/admin/v1/endpoints/{id}` | hub_viewer+ | |

#### 7.1.3. Admin API — deliveries, DLQ, replay (UI backend)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/v1/deliveries` | hub_viewer+ | Filters: `partner_id`, `status`, `event_type`, `from`, `to`, `correlation_id`, `sla_breached` |
| GET | `/admin/v1/deliveries/{id}` | hub_viewer+ | Delivery + attempts |
| GET | `/admin/v1/deliveries/{id}/attempts` | hub_viewer+ | Paginated attempts |
| POST | `/admin/v1/deliveries/{id}/replay` | hub_operator+ | Body: `{ "reason": "...", "reset_attempt_counter": false }` → audit |
| POST | `/admin/v1/deliveries/bulk-replay` | hub_admin | Stage 2: `{ "delivery_ids": [], "reason": "..." }` + rate limit |
| POST | `/admin/v1/deliveries/test` | hub_admin | Sandbox test |
| GET | `/admin/v1/dead-letters` | hub_viewer+ | DLQ list |
| POST | `/admin/v1/dead-letters/{id}/ack` | hub_operator+ | Acknowledge + audit |
| DELETE | `/admin/v1/dead-letters/{id}` | hub_admin | Purge (with audit) |

#### 7.1.4. Admin API — analytics and compliance (stage 2)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/v1/analytics/partners/{id}/summary` | success_rate, p95 latency, sla_compliance_pct, sla_breaches, circuit_state, dlq_age |
| GET | `/admin/v1/analytics/overview` | Top failing partners, DLQ counter, average compliance |
| GET | `/admin/v1/audit-logs` | Filter by action/resource (hub_admin / security) |

#### 7.1.5. Internal API (domain services)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/internal/v1/outbound/events` | `{ partner_id, event_type, payload, idempotency_key, correlation_id }` |
| GET | `/internal/v1/health` | Deep health: PG, Redis, Kafka, outbox lag |

Stage 3 fan-out: one Internal API call may create N deliveries (one per matching active outbound endpoint); response may include `delivery_ids` in addition to primary `delivery_id`.

### 7.2. Kafka topics

| Topic | Partitions | Retention | Producer | Consumer | Purpose |
|-------|------------|-----------|----------|----------|---------|
| `hub.outbound.accepted` | 12 | 7d | domain / api | ingress | Normalize before pending |
| `hub.outbound.pending` | 12 | 7d | outbox-relay / ingress | outbound-worker | Immediate delivery |
| `hub.outbound.retry.30s` | 6 | 3d | worker | outbound-worker | Tier 1 |
| `hub.outbound.retry.1m` | 6 | 3d | worker | outbound-worker | Tier 1b (delay in (45s, 90s]) |
| `hub.outbound.retry.5m` | 6 | 3d | worker | outbound-worker | Tier 2 |
| `hub.outbound.retry.15m` | 6 | 7d | worker | outbound-worker | Tier 3 |
| `hub.outbound.retry.1h` | 3 | 14d | worker | outbound-worker | Tier 4 |
| `hub.outbound.dlq` | 6 | 30d | worker | dlq-processor, alerting | Terminal failures |
| `hub.outbound.delivered` | 6 | 7d | worker | analytics (optional) | Success notifications |
| `hub.inbound.{event_type}` | 6 | 7d | inbound-api | domain consumers | Normalized inbound |
| `hub.integration.sla_breached` | 3 | 30d | worker / scheduler | alerting | SLA breaches |
| `hub.audit.events` | 3 | 90d | api | compliance sink | Audit stream (stage 2) |

**Message key:** `{partner_id}` — partition affinity (ordering per partner). On the wire the key is partner `public_id`.

**Kafka headers:** `correlation_id`, `delivery_id`, `event_type`, `attempt`, `content-type`.

### 7.3. Example `hub.outbound.pending` event schema (AsyncAPI)

Logical AsyncAPI 3.0 fragment (contract spec, not application code).
`delivery_id` / `partner_id` are `public_id` (UUIDv7), not internal `BIGINT`:

```yaml
message:
  name: OutboundPending
  payload:
    type: object
    required:
      - delivery_id
      - partner_id
      - endpoint_id
      - event_type
      - attempt
      - payload
      - idempotency_key
    properties:
      delivery_id: { type: string, format: uuid, description: deliveries.public_id }
      partner_id: { type: string, format: uuid, description: partners.public_id }
      endpoint_id: { type: string, format: uuid, description: partner_endpoints.id }
      event_type: { type: string }
      attempt: { type: integer, minimum: 1 }
      idempotency_key: { type: string }
      correlation_id: { type: string }
      payload: { type: object, additionalProperties: true }
      scheduled_at: { type: string, format: date-time }
      sla_deadline_at: { type: string, format: date-time }
```

### 7.4. OpenAPI / AsyncAPI artifacts in the repository

- `docs/openapi/openapi.yaml` — generated from FastAPI + manual overrides.
- `docs/asyncapi/asyncapi.yaml` — source of truth for Kafka contracts.
- CI: `asyncapi validate` + breaking-change detection (stage 2).

### 7.5. Outbound HTTP request format to partner

```
POST {endpoint.url}
Headers:
  Content-Type: application/json
  X-Hub-Delivery-Id: {delivery_id}
  X-Hub-Event-Type: order.created
  X-Hub-Timestamp: 1720000000
  X-Hub-Signature-256: sha256={hmac}
  Idempotency-Key: {idempotency_key}
  X-Correlation-Id: {correlation_id}
Body: { ... payload JSON ... }
```

Outbound signature: `HMAC-SHA256(primary_signing_secret, timestamp + "." + body)`.

---

## 8. Non-functional requirements

### 8.1. Performance and scale

| Parameter | Stage 1 | Stage 2 | Stage 3 |
|-----------|---------|---------|---------|
| Outbound throughput | 100 req/s sustained | 400 req/s | 2000 req/s (horizontal workers) |
| Inbound throughput | 50 req/s | 200 req/s | 500 req/s |
| P95 HTTP delivery overhead (excluding partner) | < 50 ms | < 30 ms | < 20 ms |
| Max payload size | 256 KB | 512 KB | 1 MB (configurable) |
| Kafka end-to-end lag P95 | < 5 s | < 2 s | < 1 s |
| Outbox lag P95 (stage 2) | — | < 2 s | < 1 s |

Horizontal scaling: `hub-outbound-worker` replicas = consumer group members.

**PostgreSQL (stage 3, optional):** read replica for Admin API delivery/DLQ/compliance list reads; all writes (`deliveries`, `outbox_events`, audit) on primary. Sharding not required at the target scale horizon.

**Laptop characterization (not NFR):** 2026-06-14 `make perf-up` + Locust `LOAD_WAIT_MIN`/`MAX`=0. Wave 4: outbound accept (`POST /internal/v1/outbound/events` → 202) ≈ 57 req/s at 50 users (p50 690 ms) and ≈ 62 req/s at 100 users (p50 1400 ms); health p50 290→600 ms with `hub-api` CPU pegged at 1.0. Wave 6 remesure (same overlay, rebuilt Wave 5 images, `cpus: "4.0"`): outbound ≈ 234 req/s at 50 users (p50 170 ms) and ≈ 219 req/s at 100 users (p50 380 ms); health p50 66→160 ms; limiter still API/process at the 4.0 quota. Wave 7 remesure (same overlay, rebuilt Wave 7 images — pure ASGI middleware + accept-path L1 cache): outbound ≈ 236 req/s at 50 users (p50 180 ms) and ≈ 251 req/s at 100 users (p50 350 ms); health p50 14→20 ms; limiter **DB/pool (accept-path writes)** (API 4.0 quota not pegged at 100 users). Wave 8 remesure (same overlay, rebuilt Wave 8 images — insert-first idempotency + `pool_pre_ping=False`): outbound ≈ 402 req/s at 50 users (p50 85 ms) and ≈ 418 req/s at 100 users (p50 180 ms); health p50 14→32 ms; limiter **API/process** at the 4.0 quota (health doubled; API cgroup pegged at 100 users). Persist-CTE remesure (same overlay, rebuilt images — one-statement deliveries+outbox INSERT): outbound ≈ 373 req/s at 50 users (p50 100 ms) and ≈ 421 req/s at 100 users (p50 200 ms); health p50 16→31 ms; limiter still **API/process** at the 4.0 quota. Isolated Kafka pending drain (lag=0 before each 60s hold): after Locust stop, `hub.outbound.pending` lag ≈ 16k (50 users) / ≈ 18k (100 users) reached 0 in ≈ **258 s** / **284 s** (one assigned worker ≈ 69 msg/s). These facts do **not** replace the Stage 1/2/3 table above. Evidence: [`docs/perf/ceiling-prodlike.md`](docs/perf/ceiling-prodlike.md), [`docs/perf/ceiling-remeasure.md`](docs/perf/ceiling-remeasure.md), [`docs/perf/ceiling-accept-path.md`](docs/perf/ceiling-accept-path.md), [`docs/perf/ceiling-db-roundtrip.md`](docs/perf/ceiling-db-roundtrip.md), [`docs/perf/ceiling-persist-cte.md`](docs/perf/ceiling-persist-cte.md), [`docs/perf/ceiling-kafka-lag-drain.md`](docs/perf/ceiling-kafka-lag-drain.md).

### 8.2. Availability and reliability

- **Target availability:** 99.9% (stage 2 prep-stand benchmark).
- Stateless API behind a load balancer; workers scale independently.
- PostgreSQL: daily backups, PITR in docs (Compose: volume backup script).
- Kafka: RF=3 in prod docs; RF=1 allowed in stage 1 Compose (one broker).
- Graceful shutdown: workers finish in-flight delivery (max 30 s) before SIGTERM.
- **Delivery reliability:** idempotency, capped retries, DLQ, **transactional outbox** (stage 2).

### 8.3. Security

| Requirement | Implementation |
|-------------|----------------|
| Secrets at rest | `signing_secret` encrypted (Fernet / pgcrypto); API keys — hash only |
| Secrets in transit | TLS 1.2+ |
| Partner auth (inbound) | API key + HMAC |
| Secret rotation | primary/previous overlap window; audit; partner notify (stage 2) |
| Admin auth | JWT RS256 / API key with RBAC scopes |
| Rate limiting | Redis token bucket: inbound/outbound/replay per partner |
| Input validation | Pydantic v2 strict; max body size |
| Audit | All replay, key rotations, DLQ purge |
| Dependency scanning | GitHub Dependabot |
| Secrets not in git | Environment / secret store only; `cp .env.example .env` for local |

### 8.4. Idempotency and consistency

- Semantics: **at-least-once** for Kafka and HTTP retries.
- Outbound duplicates allowed; partner must honor `Idempotency-Key`.
- DB transactions: create delivery + **outbox** in one transaction (stage 2) — table `outbox_events` + relay.

### 8.5. Observability (production-ready)

#### 8.5.1. Logging (structlog)

Required fields: `timestamp`, `level`, `service`, `correlation_id`, `delivery_id`, `partner_id`, `event_type`, `attempt`, `duration_ms`, `http_status`, `sla_breached`.

Levels: INFO — main success path, WARNING — retry, ERROR — terminal failure.

#### 8.5.2. Minimum Prometheus metrics

| Metric | Type | Labels |
|--------|------|--------|
| `hub_deliveries_total` | Counter | `partner_id`, `status`, `event_type` |
| `hub_delivery_attempts_total` | Counter | `partner_id`, `http_status_class` |
| `hub_delivery_duration_seconds` | Histogram | `partner_id` |
| `hub_dlq_messages_total` | Counter | `partner_id`, `reason` |
| `hub_dlq_backlog` | Gauge | `partner_id` |
| `hub_dlq_oldest_age_seconds` | Gauge | — |
| `hub_replay_total` | Counter | `trigger` (manual/scheduled) |
| `hub_circuit_breaker_state` | Gauge | `partner_id`, `state` |
| `hub_inbound_events_total` | Counter | `partner_id`, `event_type` |
| `hub_inbound_duplicate_suppressed_total` | Counter | `partner_id` |
| `hub_rate_limit_rejected_total` | Counter | `partner_id`, `direction` |
| `hub_sla_breaches_total` | Counter | `partner_id`, `event_type` |
| `hub_sla_compliance_ratio` | Gauge | `partner_id` |
| `hub_outbox_unpublished` | Gauge | — |
| `hub_kafka_consumer_lag` | Gauge | `topic`, `group` |

#### 8.5.3. Grafana dashboards

1. **Integration Health Overview** — success share, throughput, error rate, top failing partners.
2. **SLA & Compliance** — P50/P95/P99, compliance share, `sla_breaches`, deadlines.
3. **DLQ & Replay** — backlog, oldest age, replay success rate, unacked DLQ, **DLQ growth trend**.
4. **Infrastructure** — Kafka lag, **outbox lag**, PG connections, Redis memory, worker CPU.

#### 8.5.4. Alerts (minimum)

| Alert | Condition | Action (runbook) |
|-------|-----------|------------------|
| DLQ growth | rate(`hub_dlq_messages_total`) above threshold 5 min | Open DLQ panel; classify poison vs outage |
| DLQ age | `hub_dlq_oldest_age_seconds` > 1 h | Escalate SRE; check circuit |
| Success rate | < 99% over 15 min for tier-1 partners | Check attempts, mock/partner |
| Compliance drop | `hub_sla_compliance_ratio` < 98% over 1 h | BizDev/SRE; prepare SLA facts |
| Circuit open | open > 5 min for critical partner | Contact partner; pause bulk-replay |
| Outbox lag | `hub_outbox_unpublished` growing | Check relay; silent under-delivery risk |
| Kafka lag | `hub_kafka_consumer_lag` > 10000 | Scale workers |

### 8.6. Partner isolation (**multi-tenant**)

- All requests scoped by `partner_id`.
- Row-level security (stage 3, optional) for `partner_api` read own data.
- No cross-partner leakage in logs (secret masking).

### 8.7. Compliance and data retention

- Payload retention: 90 days default, configurable per partner.
- GDPR: delete/anonymize partner data on request (cascade by `partner_id`).
- Audit logs: retention 1 year.
- `sla_breached` facts retained at least for claims work (config, default 1 year).

### 8.8. Production-ready checklist (summary)

| Area | Hub requirement |
|------|-----------------|
| Delivery reliability | Idempotency, Kafka retries, DLQ, **outbox** (stage 2) |
| Observability | structlog, Prometheus, Grafana, DLQ/SLA alerts |
| Security | HMAC, rotation, RBAC, audit, secrets out of git |
| Operations | health probes, graceful shutdown, Alembic, env-config, CI gate |
| Multi-tenancy | isolation by `partner_id` |
| Business KPI | success rate, SLA compliance, MTTR |
| Conscious boundaries | no Partner Portal, no exactly-once e2e, no penalty math |
| Demo 5–10 min | failure → retry → DLQ → replay → HMAC/idempotency |

---
## 9. Repository structure

```
2_b2b_partner_integration_hub/
├── README.md                          # EN: overview, quickstart, architecture
├── spec.md                            # This document (product SoT)
├── pyproject.toml                     # uv/poetry, Ruff, mypy
├── alembic.ini
├── docker-compose.yml
├── docker-compose.test.yml
├── .github/
│   └── workflows/
│       ├── ci.yml                     # lint, mypy, test, contract
│       └── release.yml                # optional: publish images
├── docs/
│   ├── adr/                           # ADR-001..010
│   ├── openapi/
│   │   └── openapi.yaml
│   ├── asyncapi/
│   │   └── asyncapi.yaml
│   ├── runbooks/
│   │   ├── dlq-response.md
│   │   ├── replay-procedure.md
│   │   ├── sla-breach-response.md
│   │   └── partner-onboarding.md
│   └── grafana/
│       └── dashboards/
│           ├── integration_health.json
│           ├── sla_compliance.json
│           └── dlq_replay.json
├── infra/
│   ├── prometheus/
│   │   ├── prometheus.yml
│   │   └── alerts.yml                 # DLQ growth, SLA, circuit
│   ├── grafana/
│   │   └── provisioning/
│   └── kafka/
│       └── create-topics.sh
├── scripts/
│   ├── seed_partners.py
│   └── generate_openapi.py
├── app/
│   ├── __init__.py
│   ├── main.py                        # FastAPI app factory
│   ├── config.py                      # Pydantic Settings
│   ├── logging.py                     # structlog
│   ├── dependencies.py
│   ├── domain/
│   │   ├── models/
│   │   │   ├── partner.py
│   │   │   ├── endpoint.py
│   │   │   ├── delivery.py
│   │   │   ├── attempt.py
│   │   │   ├── dead_letter.py
│   │   │   ├── inbound_event.py
│   │   │   ├── outbox.py
│   │   │   └── audit.py
│   │   ├── enums.py
│   │   └── services/
│   │       ├── delivery_service.py
│   │       ├── replay_service.py
│   │       ├── hmac_service.py
│   │       ├── secret_rotation.py
│   │       ├── circuit_breaker.py
│   │       ├── rate_limiter.py
│   │       ├── sla_service.py
│   │       └── backoff.py
│   ├── api/
│   │   ├── v1/
│   │   │   ├── inbound/
│   │   │   ├── admin/
│   │   │   └── internal/
│   │   └── middleware/
│   │       ├── correlation.py
│   │       └── auth.py
│   ├── workers/
│   │   ├── outbound_consumer.py
│   │   ├── dlq_consumer.py
│   │   └── outbox_relay.py            # stage 2
│   ├── integrations/
│   │   ├── http_client.py
│   │   ├── kafka_producer.py
│   │   └── kafka_consumer.py
│   ├── schemas/
│   │   ├── partner.py
│   │   ├── delivery.py
│   │   └── events.py
│   └── db/
│       ├── session.py
│       └── migrations/
├── admin_ui/                          # thin SPA; Admin API calls only
│   ├── package.json
│   ├── src/
│   │   ├── pages/
│   │   │   ├── DeliveriesList.tsx
│   │   │   ├── DeliveryDetail.tsx
│   │   │   ├── DeadLetters.tsx
│   │   │   ├── PartnersList.tsx
│   │   │   └── PartnerCompliance.tsx  # stage 2
│   │   └── api/
│   │       └── client.ts
│   └── vite.config.ts
├── celery_app/
│   ├── app.py
│   └── tasks/
│       ├── replay.py
│       ├── maintenance.py
│       ├── secret_rotation.py
│       └── beat_schedule.py
└── tests/
    ├── unit/
    │   ├── test_hmac.py
    │   ├── test_backoff.py
    │   ├── test_status_machine.py
    │   ├── test_circuit_breaker.py
    │   ├── test_rate_limiter.py
    │   └── test_sla.py
    ├── integration/
    │   ├── test_outbound_flow.py
    │   ├── test_inbound_idempotency.py
    │   ├── test_replay_audit.py
    │   ├── test_outbox_relay.py
    │   └── test_fault_injection.py
    ├── contract/
    │   ├── test_openapi_partner_mock.py
    │   └── test_asyncapi_schemas.py
    └── fixtures/
        ├── partner_factory.py
        └── kafka_helpers.py
```

Frozen local ports (Compose): API `8000`, UI `8080`, PG `5432`, Redis `6379`, Kafka `9092`, OTLP `4317`/`4318`, Prometheus `9090`, Grafana `3000`, Jaeger `16686`, partner-mock `8090`.

---

## 10. Testing and CI

### 10.1. Test pyramid

| Level | Share | Focus |
|-------|-------|-------|
| Unit | 55% | HMAC, backoff, status machine, circuit breaker, rate limit, SLA clock, Pydantic |
| Integration | 30% | API + PG + Redis + Kafka (testcontainers), outbound/inbound, outbox, replay+audit |
| Contract + fault injection | 15% | OpenAPI/AsyncAPI, partner mock, controlled failures |

**Target coverage:** ≥ 85% on `app/domain`, `app/api`, `app/workers`.

### 10.2. Contract tests

1. **OpenAPI:** schemathesis or pytest + spec — Admin/Inbound honor the schema.
2. **Partner mock:** WireMock/FastAPI expects `X-Hub-Signature-256`, `Idempotency-Key`.
3. **AsyncAPI:** each producer topic validates payload against JSON Schema from `docs/asyncapi/`.
4. **Breaking change gate:** required-field change in AsyncAPI without envelope version bump → CI fail.
5. **Secret rotation:** mock accepts previous secret in overlap window and rejects after revoke.

### 10.3. Fault-injection scenarios (integration)

| Scenario | Expectation |
|----------|-------------|
| Partner 503 × 3, then 200 | `delivered` after retries; compliance on `first_success_at` |
| Partner 400 | Immediate `failed` + DLQ, no retry; DLQ alert/metric |
| Timeout | Retry scheduled, attempt logged |
| Duplicate inbound Idempotency-Key | One Kafka message |
| Circuit open | Deliveries paused, gauge=1 |
| Manual replay after fix | `delivered` from `failed` + `audit_logs` row |
| Kafka publish fails after delivery insert (without outbox / with **outbox**) | Stage 1: discrepancy metric; stage 2: relay catches up, no silent loss |
| Burst replay | Rate limit prevents storm; partial success with audit |
| DLQ growth | Alert rule fires on synthetic metrics |

### 10.4. CI/CD pipeline (GitHub Actions)

On push/PR (`ci.yml`):

1. **lint** — `ruff check`, `ruff format --check`
2. **typecheck** — `mypy app celery_app`
3. **unit** — `pytest tests/unit -q`
4. **integration** — `docker compose -p b2b-partner-integration-hub -f docker-compose.test.yml up -d`, `pytest tests/integration`
5. **contract** — `pytest tests/contract`, `asyncapi validate docs/asyncapi/asyncapi.yaml`
6. **coverage** — fail if < 85% on critical paths

On main (optional): build images `hub-api`, `hub-worker`, `hub-admin-ui`, `hub-outbox-relay`; push to GHCR.

### 10.5. Local development

One `docker compose up` starts api, **worker**, (`outbox-relay` at stage 2), kafka, postgres, redis, prometheus, grafana, admin-ui, partner-mock. Alembic migrations, partner seed, worker start — per README. Shell commands are not duplicated in this spec as a mandatory application script.

### 10.6. Pre-commit (recommendation)

- ruff format, ruff check, mypy on staged `app/`.

---

## 11. Definition of Done

Mid+/Senior acceptance level (Definition of Done).

### 11.1. Functional criteria

- [ ] CRUD partners/endpoints via Admin API with RBAC; partner has `sla_seconds`.
- [ ] Outbound: event → HTTP → `delivered` on 2xx.
- [ ] Retry: transient → exponential backoff → success within `max_attempts`.
- [ ] DLQ: after exhausted attempts — `dead_letters` + topic `hub.outbound.dlq`.
- [ ] Non-retryable 4xx → immediate DLQ without extra attempts.
- [ ] Inbound: API key + HMAC + idempotency + rate limit per this spec.
- [ ] Admin replay: single and bulk (stage 2) with mandatory `reason` and `audit_logs` row.
- [ ] Celery scheduled replay for `auto_replay_enabled` with audit `trigger=scheduled`.
- [ ] State machine — only valid transitions.
- [ ] API keys: hash storage, revoke; signing secret: rotation with overlap (stage 2).
- [ ] SLA: `first_success_at`, `sla_breached`, breach event/metric, compliance summary (stage 2).
- [ ] **Transactional outbox** + relay (stage 2) — no silent delivery→Kafka loss.
- [ ] Thin admin UI: delivery list, attempts card, DLQ, replay button.

### 11.2. Technical criteria (Senior signal)

- [ ] Kafka retry topics + DLQ documented and implemented.
- [ ] Circuit breaker per partner in Redis.
- [ ] Rate limit per partner (inbound/outbound/replay).
- [ ] httpx: connect/read timeouts; no unbounded POST retries.
- [ ] **Transactional outbox** for delivery+publish (stage 2).
- [ ] OpenAPI + AsyncAPI in `docs/`, validated in CI.
- [ ] ≥ 85% coverage; contract tests and fault injection green.
- [ ] Prometheus ≥ 12 metrics; ≥ 3 importable Grafana dashboards; DLQ growth alert.
- [ ] structlog JSON with `correlation_id` through API → **worker** → HTTP.
- [ ] Docker Compose one-command up for demo (including admin UI).
- [ ] README (EN): architecture, trade-offs, failure modes, demo scenario, SLA narrative.
- [ ] Ruff + mypy strict with no errors in `app/`.
- [ ] ADR at least 5 documents; recommended 9+ (including **outbox**, SLA compliance, identifier policy, and multi-URL fan-out).

### 11.3. Local Compose demo walkthrough (5–10 minutes)

1. `docker compose up`.
2. Create partner + **endpoint** (API or UI) with `sla_seconds`.
3. Send outbound → show `delivered` in UI/API.
4. Enable mock 503 → show retries and recovery.
5. Exhaust attempts → DLQ in UI + Grafana (DLQ growth).
6. Replay with reason → `delivered` + audit record.
7. Inbound with bad signature → 403.
8. Repeat inbound with same Idempotency-Key → 200 duplicate.
9. (stage 2) Show SLA compliance panel / DLQ alert.

| Minute | Action | What it proves |
|--------|--------|----------------|
| 0–1 | `docker compose up`, open admin UI | One-command demo |
| 1–2 | Create partner/endpoint with SLA | Registry without core code change |
| 2–3 | Test outbound → `delivered` | Happy path + status |
| 3–5 | Mock 503 → retries → recovery | Backoff / retry topics |
| 5–7 | Exhaust attempts → DLQ in UI + Grafana (growth) | Poison/terminal isolation + alert |
| 7–8 | Replay with reason → `delivered` + audit | Operational MTTR and compliance |
| 8–9 | Inbound: bad HMAC → 403; duplicate key → 200 | Security + idempotency |
| 9–10 | AsyncAPI/OpenAPI + SLA/DLQ panels | Contract + observability + SLA pain |

---

## 12. ADR index summaries

ADR-style block: “why this way”, alternatives, trade-offs. Complements the short table in §4.12.

### 12.1. ADR-001 — Delivery semantics: at-least-once, not exactly-once

**Context:** stakeholder wants “guaranteed delivery without duplicates”.

**Decision:** **at-least-once** semantics + partner idempotency contract (`Idempotency-Key`).

**Alternatives:** Kafka transactions + exactly-once processing inside the cluster; Hub-only dedup without partner cooperation.

**Why:** end-to-end exactly-once for HTTP webhooks is unrealistic without receiver cooperation (timeouts, retry after success, URL change). Document the partner’s duty to deduplicate.

**Trade-off:** HTTP duplicates possible; mitigated by the same idempotency key on replay.

### 12.2. ADR-002 — Retries via Kafka topics, not Celery countdown

**Context:** need delay between attempts and observability.

**Decision:** separate topics `hub.outbound.retry.{tier}` + backoff policy on the **endpoint**.

**Alternatives:** Celery ETA/countdown; Redis delayed queue (ZSET); single topic with sleep in consumer.

**Why:** tier isolation, retention, lag metrics, natural DLQ topic, AsyncAPI contracts. Celery remains for **scheduled** tasks only. Kafka retries are not Celery countdown.

**Trade-off:** more topics and operational complexity; pays off at 500k–2M deliveries/day.

### 12.3. ADR-003 — PostgreSQL as source of truth + Kafka as bus

**Context:** need Admin API/UI with statuses, attempts, filters, compliance.

**Decision:** SoT in PostgreSQL; Kafka — transport and event log. Client: **aiokafka**.

**Alternatives:** pure event sourcing; status only in Redis.

**Why:** convenient queries, transactions, indexes for Support and BizDev. Event sourcing complicates MVP without gain for a webhook hub.

**Trade-off (stage 2):** **transactional outbox** so events are not lost on dual-write PG↔Kafka.

### 12.4. ADR-004 — Stripe-style HMAC-SHA256 + API key + rotation

**Context:** authenticate and integrity-check inbound/outbound; Security requires rotation.

**Decision:** `HMAC-SHA256(secret, timestamp + "." + body)` + Bearer API key; skew ±300 s; constant-time compare; primary/previous window on rotation.

**Alternatives:** mTLS only; IP allowlist only; JWT in body.

**Why:** industry webhook standard, clear to partners, well testable. mTLS is a perimeter complement (stage 3), not an HMAC replacement.

**Trade-off:** rotation discipline and timestamp anti-replay required; overlap window reduces downtime risk.

### 12.5. ADR-005 — Circuit breaker per partner in Redis

**Context:** partner is down → retry storm hits them and our SLA budget.

**Decision:** closed/open/half-open in Redis with TTL; pause outbound when open.

**Alternatives:** global rate limit; manual **endpoint** pause; no CB.

**Why:** localizes failure to the partner; gives a metric and runbook (“partner is open”).

**Trade-off:** if Redis is down — safe fallback: **fail-open** (keep delivering) with DB rate limit, and document it.

### 12.6. ADR-006 — Thin admin UI over Admin API

**Context:** demo and Support/SRE MTTR; pain “cannot see whether it arrived”.

**Decision:** light SPA on **Vite + React + TypeScript** (`admin_ui/`), read/commands only via Admin API; **no** retry/DLQ/HMAC business logic on the client. HTMX / server-rendered HTML is not the preferred path.

**Alternatives:** OpenAPI + Grafana only; heavy Partner Portal.

**Why:** backend remains the primary integration surface; UI proves status/replay value in minutes of demo. Partner Portal is out of scope.

### 12.7. ADR-007 — Transactional outbox for outbound deliveries

**Context:** dual-write PG+Kafka → silent losses → SLA penalties with no DLQ trail.

**Decision:** `outbox_events` in the same transaction as `deliveries`; relay publishes asynchronously.

**Alternatives:** sync publish after commit; Kafka transactions as SoT; CDC.

**Why:** simple, testable pattern; unpublished metric; removes a class of silent failures at the DB/bus boundary.

**Trade-off:** another process (`hub-outbox-relay`); deliberately simplified at stage 1.

### 12.8. ADR-008 — SLA compliance inside the Hub

**Context:** contract penalties exist; measurement does not.

**Decision:** `sla_seconds` / `sla_deadline_at` / `first_success_at` / `sla_breached` + metrics + event `hub.integration.sla_breached` + compliance panel. SLA clock = `first_success_at`.

**Alternatives:** external BI only; manual reports from logs.

**Why:** operationalize “SLA penalty” pain; alerts before customer escalation; facts for Finance/Legal.

**Trade-off:** Hub does not compute penalty money — only facts; legal interpretation stays outside.

### 12.9. ADR-009 — UUIDv7, dual-id, and no composite PK

**Context:** need stable external IDs for API/replay, compact FKs and indexes, without confusing “UUID vs natural key”.

**Decision:** UUIDv7 instead of v4; dual-id (`BIGINT` PK + `public_id` UUIDv7) **only** for `partners` and `deliveries`; other entities from §6.3 matrix — UUIDv7 PK or `BIGINT` PK for `outbox_events`. No composite PK: `(partner_id, idempotency_key)` and `(delivery_id, attempt_number)` are `UNIQUE`, not PK.

**Alternatives:** UUID v4 everywhere; dual-id on all tables; composite PK `(partner_id, …)` on tenant-like tables.

**Why:**
- UUIDv7 is time-ordered (less random B-tree churn than v4) and works as a public ID.
- Dual-id is limited to hot-path entities with frequent FKs and an external contract: compact `BIGINT` inside, opaque `public_id` outside (API, Kafka, replay).
- Composite PK on tenant-like tables complicates ORM/FK (cascades, joins, migrations) and mixes business uniqueness with a surrogate key; natural `UNIQUE` is enough for idempotency.

**Trade-off:** resolve `public_id` → internal `id` at the API boundary; discipline not to expose `BIGINT` externally.

### 12.10. ADR-010 — Multi-endpoint event_type fan-out

**Context:** partners may register multiple active outbound URLs for the same `event_type`. Selecting only the first matching endpoint leaves other URLs undelivered. Deliveries keep `UNIQUE (partner_id, idempotency_key)`.

**Decision:** fan-out to all matching active outbound endpoints; one request creates N `deliveries` + N `outbox_events` in one transaction. Stored `idempotency_key = {client_key}::{endpoint.public_id}`; caller key as `source_event_id`. Strict duplicate on existing `source_event_id` for the partner (no retroactive rows for endpoints added later).

**Alternatives:** change UNIQUE to include `endpoint_id`; per-endpoint duplicate only when all endpoints already have rows.

**Why:** preserves Stage 1/2 uniqueness constraint; callers keep a single idempotency key; operator tools target delivery `public_id` per URL.

**Trade-off:** endpoint add/remove after first accept does not retroactively deliver under the same caller key.

### 12.11. Error classification: retryable vs non-retryable

**Q:** Why does 422 go straight to DLQ while 503 retries?

**A:** 422/400 are almost certainly poison for the same payload; retry only burns SLA budget and clogs logs. 5xx/timeout are transient. `retry_on_status_codes` is configurable per **endpoint**. Poison taxonomy: 408/429/5xx retry; 400/401/403/404/422 → DLQ.

### 12.12. Ordering and partition key

**Q:** Why is the message key = `partner_id`?

**A:** Preserves order of one partner’s events in a partition (important for `order.created` → `order.updated`). In Kafka the key is the partner `public_id`. Cost — hot partitions for large partners; mitigated by more partitions and separate **endpoint**/event routing (stage 3).

### 12.13. Frequent product questions

| Question | Short answer |
|----------|--------------|
| Why not iPaaS (MuleSoft/Boomi)? | Hub is a narrow, controlled webhook delivery layer with full observability in our perimeter; cheaper and clearer for SaaS core. |
| Do you guarantee delivery within N seconds? | We operationalize SLA (`sla_seconds`), alert on breach, and measure compliance; legal penalties are outside the Hub. |
| Can payload change on replay? | Default no (same payload + Idempotency-Key). Edits only via a new source event (avoid silent audit substitution). |
| Who cleans DLQ? | SRE ack / purge with audit; scheduled auto-replay only if `auto_replay_enabled` and circuit closed. |
| Why rate-limit replay? | So “healing” an outage does not become a second storm and a new SLA-breach wave. |

---

## 13. Operator UI

### 13.1. Is a UI needed?

**Yes — recommend a thin admin/demo UI.** The backend remains the primary integration surface; the UI accelerates demo and operational MTTR for deliveries, DLQ, and replay.

A full Partner Portal (partner self-service) is **out of scope**.

### 13.2. UI goal

Give SRE/Support in minutes:

- find a delivery by `delivery_id` / `correlation_id` / partner / SLA-breach flag;
- see attempts and last error;
- replay with mandatory reason (audit on backend);
- view and ack DLQ;
- see simple partner health: success rate, compliance, circuit state (stage 2).

### 13.3. Screens (3–7)

| # | Screen | Actions |
|---|--------|---------|
| 1 | Delivery list | Filters: partner, status, event_type, period, sla_breached; link to detail |
| 2 | Delivery detail | Payload (masked), attempts timeline, replay / copy id buttons |
| 3 | DLQ list | Reason, age, ack, jump to delivery |
| 4 | Partners (read + basic stage-1 CRUD) | Status, SLA, link to endpoints |
| 5 | Test delivery (sandbox) | Send test event |
| 6 | (stage 2) Partner compliance summary | success_rate, SLA breaches, circuit state, rate limit |
| 7 | (stage 2) Bulk replay | Select several failed + reason |

### 13.4. What the UI does NOT do

- Does not implement retry/backoff/circuit breaker/HMAC/**outbox**.
- Does not write directly to Kafka or PostgreSQL.
- Does not store partner secrets in the browser beyond the session.
- Does not replace Grafana (aggregate alerts and infra live there).

All logic is on Admin API; UI only displays and issues commands.

### 13.5. UI stack (light)

- **Vite + React + TypeScript** (sole standard; no HTMX/Jinja as preferred).
- Calls only `/admin/v1/*` with JWT/API key.
- No complex “growth” state manager; minimal dependencies; OpenAPI client generation desirable.
- Built as separate Compose container `hub-admin-ui`.

### 13.6. How the UI proves backend value

On a local demo an operator sees the status machine, attempts, and DLQ workflow that would otherwise require SQL/curl. One-click replay with reason shows the audit/SLA path. Emphasis remains: **core value is Kafka retries, DLQ, HMAC, transactional outbox, SLA compliance, metrics, and alerts**.

---

## 14. Appendices

### Appendix A — Default configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HUB_MAX_ATTEMPTS_DEFAULT` | 8 | Max delivery attempts |
| `HUB_BACKOFF_BASE_SECONDS` | 30 | Exponential base |
| `HUB_BACKOFF_MULTIPLIER` | 2 | Multiplier |
| `HUB_BACKOFF_MAX_SECONDS` | 3600 | Delay ceiling |
| `HUB_INBOUND_TIMESTAMP_TOLERANCE` | 300 | Skew tolerance, seconds |
| `HUB_HTTP_CONNECT_TIMEOUT_MS` | 3000 | httpx connect |
| `HUB_HTTP_READ_TIMEOUT_MS` | 10000 | httpx read |
| `HUB_IDEMPOTENCY_TTL_HOURS` | 24 | Redis TTL |
| `HUB_CIRCUIT_FAILURE_THRESHOLD` | 10 | Failure threshold in window |
| `HUB_CIRCUIT_WINDOW_SECONDS` | 60 | Counting window |
| `HUB_CIRCUIT_OPEN_SECONDS` | 300 | Open duration |
| `HUB_RATE_LIMIT_RPS_DEFAULT` | 100 | Per-partner limit |
| `HUB_SECRET_ROTATION_OVERLAP_HOURS` | 24 | Previous-secret window |
| `HUB_SLA_SECONDS_DEFAULT` | 60 | Default partner SLA |
| `HUB_DLQ_AGE_ALERT_SECONDS` | 3600 | DLQ age alert threshold |

### Appendix B — Partner onboarding checklist

1. Create `partner` (slug, SLA, circuit config, rate limit).
2. Issue `api_key` and `signing_secret`; deliver to partner out-of-band.
3. Register outbound `endpoint` (URL, event_types, backoff, SLA override if needed).
4. Run sandbox test delivery; verify signature on partner side.
5. Run test inbound with correct HMAC and Idempotency-Key.
6. Set `status=active`; subscribe to success rate / DLQ growth / SLA compliance alerts.
7. Add partner to Grafana folder / recording rules (stage 2).
8. Agree secret-rotation procedure and overlap window.

### Appendix C — DLQ growth response checklist (runbook)

1. Alert `hub_dlq_messages_total` rate / `hub_dlq_oldest_age_seconds`.
2. Open dead-letter card in UI → last HTTP status / error.
3. Classify: poison (payload/contract bug) vs partner outage.
4. Poison → ticket to domain/integrations; do not spin auto-replay without a fix.
5. Outage → wait for half-open/closed; manual replay or Celery auto-replay with rate limit.
6. Ack DLQ after successful replay or deliberate purge with audit reason.
7. Check whether `sla_breached` accumulated — prepare BizDev facts if needed.

### Appendix D — SLA breach response checklist

1. Alert / growth of `hub_sla_breaches_total` or drop of `hub_sla_compliance_ratio`.
2. Filter deliveries `sla_breached=true` for the period in Admin UI/API.
3. Correlate with DLQ, circuit open, Kafka / **outbox** lag.
4. If Hub systemic failure — restore first + postmortem.
5. If partner outage — communicate + controlled replay.
6. Keep a fact export for Finance/Legal (without computing the penalty yourself).

---

*End of technical requirements v3.1 EN. Next step: human release commit; capabilities for stages 1–3 are in the codebase.*
