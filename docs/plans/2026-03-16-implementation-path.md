# Implementation Path — Partner Integration Hub

**Status:** Accepted roadmap
**Date:** 2026-03-16
**Spec:** `spec.md` v3.1 EN

This document is **not** a bite-sized code plan. It describes how the product is built through multi-agent SDD: artifacts, dependencies, and quality expectations. Executable tasks live in stage plans under `docs/plans/`.

---

## 1. Purpose

Build a B2B Partner Integration Hub: centralized webhook delivery with at-least-once semantics, Kafka retry topics, DLQ, audited replay, HMAC, dual-id on partners/deliveries, and SLA **measurement** (not penalty math). Observability: OpenTelemetry SDK → Collector → Prometheus + **Jaeger** + Grafana.

---

## 2. High-level phases

| Phase | Deliverable | Tracked output |
|-------|-------------|----------------|
| Scaffolding | Agent entry + ADRs 001–009 | `AGENTS.md`, `docs/agentic/*`, `docs/adr/*` |
| CREATE Stage1 | Executable Stage1 plan | `docs/plans/2026-03-14-stage1-implementation-plan.md` |
| Execute Stage1 | MVP hub + `/docs` + Compose demo | `app/`, workers, UI, Grafana overview |
| CREATE Stage2 | Industrial plan | `docs/plans/*-stage2-implementation-plan.md` |
| Execute Stage2 | Outbox, CB, retry tiers, Celery, rotation | relay catch-up evidence |
| CREATE Stage3 | Enterprise plan | `docs/plans/2026-03-28-stage3-implementation-plan.md` |
| Execute Stage3 | Routing, partner status API, k6, Kafka traceparent | `docs/perf/` |

**Hard dependencies:**

- No domain code before ADRs + Stage1 plan exist.
- Stage2 planning starts after Stage1 evidence file is written (not a human “Done” label).
- Git: init yes; commit/push only on explicit human command.

---

## 3. Locked product choices (not open for debate)

| Topic | Choice |
|-------|--------|
| Layout | One Python package `app/` (not a three-service mesh) |
| Kafka client | **aiokafka** (ADR-003) |
| Pagination | `limit` + `offset` (default 50, max 200) |
| Correlation | Generate UUIDv7 if missing; invalid → 422; echo `X-Correlation-Id` |
| Admin auth Stage 1 | `ADMIN_BOOTSTRAP_TOKEN` → HS256 JWT stub; RBAC roles as spec §2.2 |
| Stage 1 secrets | Encrypted `signing_secret` column on `partners`; API keys hashed |
| Observability | OTel → Collector → Prometheus + Jaeger; **not Tempo** |
| Metric identity | `partner_slug`, never UUID/`delivery_id` as attributes |

**Rejected:** SOAP/FTP/EDI, Partner Portal, service mesh, OIDC/Keycloak as Stage 1 Must, Celery as webhook transport, composite PKs, copying OFOM/billing/SSO invariants.

---

## 4. Quality bar

- Live FastAPI `/docs` is the HTTP contract.
- TDD for domain/API/workers.
- Implementer ≠ Reviewer.
- Fault-injection spec §10.3 as tests.
- Do not declare Stage Done; write a local gitignored Stage N DoD evidence file.
