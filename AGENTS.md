# AGENTS.md — Partner Integration Hub

**Entry point** for all agent and subagent development in this repository per [`spec.md`](spec.md) **v3.1 EN**. Do **not** duplicate the spec as `TZ.md`.

Before any Task, phase, plan, review, or code change, the agent (parent and subagent) **must** rely on this file: §1–9 — standing rules; §10 — operational links for the active stage. Details live in [`docs/`](docs/) — only invariants, navigation, and responsibilities are here; discrepancies with `docs/` are resolved by updating **this** file (see §0.3).

| Next | Path |
|------|------|
| Product / DoD / stack | [`spec.md`](spec.md) |
| Implementation path | [`docs/plans/2026-06-01-implementation-path.md`](docs/plans/2026-06-01-implementation-path.md) |
| Workflow | [`docs/agentic/workflow.md`](docs/agentic/workflow.md) |
| Skills | [`docs/agentic/skills-map.md`](docs/agentic/skills-map.md) |
| Stage roadmap | [`docs/agentic/stage-roadmap.md`](docs/agentic/stage-roadmap.md) |
| Subagent roles | [`docs/agentic/role-prompts/`](docs/agentic/role-prompts/) |
| Phase prompts (local) | §10.1 |

---

## 0. Reading order and `docs/` map

### 0.1. Required reading order

1. **`AGENTS.md`** (this file) — environment, invariants, orchestration, gates, anti-patterns, stage §10.
2. **`spec.md`** — affected product §§, DoD §11, stack §5, structure §9, tests §10, ADR summaries §12.
3. **`docs/adr/`** — Accepted ADRs (+ amendments) relevant to the task.
4. **`docs/plans/`** — active implementation plan (Files, Steps, Acceptance) when present.
5. **`docs/agentic/`** — workflow, skills, role-prompts. Phase prompts per §10.1 live in the local gitignored SDD harness (not product docs).
6. As needed: **`docs/runbooks/`**, **`docs/slo.md`**, **`docs/architecture.md`**, root README/CONTRIBUTING.

A subagent receives a self-contained brief; if the brief references spec/ADR/`docs/` — read those files, do not rely on parent "memory".

### 0.2. `docs/` map (what to use)

| Section | Purpose | When to read |
|---------|---------|--------------|
| [`docs/adr/`](docs/adr/) | Architecture decisions (at-least-once, Kafka retries, PG SoT + aiokafka, HMAC, CB, thin UI, outbox, SLA clock, dual-id, multi-URL fan-out) | Any task touching a pattern; Gate A |
| [`docs/plans/`](docs/plans/) | Implementation path + stage-by-stage plans | Orchestration and Implementer of active stage |
| [`docs/agentic/`](docs/agentic/) | Workflow, skills, role-prompts | Subagent dispatch |
| Local SDD phase prompts (gitignored) | Phase / harness prompts | Phase start; not product docs |
| [`docs/openapi/`](docs/openapi/) | Snapshot + pointer README only — live contract is `/docs` | HTTP / contract tests |
| [`docs/asyncapi/`](docs/asyncapi/) | Kafka SoT (`asyncapi.yaml`) | Kafka envelope / CI Stage 2+ |
| [`docs/runbooks/`](docs/runbooks/) | DLQ, replay, SLA breach, partner onboarding, circuit-breaker, outbox-lag, secret-rotation, load-testing | Ops, NFR §8 |
| [`docs/perf/`](docs/perf/) | Recorded load-test facts (k6 persist path, Locust accept smoke, Locust OTEL / k6 RW, prod-like ceiling hunt). Laptop numbers are not spec §8.1 NFR | Observability, load waves |
| [`docs/slo.md`](docs/slo.md) | SLI→SLO, metric names (partner **slug** attributes), alert thresholds, dashboard interpretation | Observability, DoD, NFR |
| [`docs/architecture.md`](docs/architecture.md) | C4 from spec §4; Kafka RF=3 prod vs RF=1 Compose | Onboarding |
| `.superpowers/sdd/progress.md` | Task↔spec linkage (gitignored local SDD harness; no Task N in app code) | After each Task |

Conflict priority: **product invariant** → `spec.md` + Accepted ADR; **Task / Acceptance order** → active plan; **agent operational rules** → `AGENTS.md`; **library APIs** → official docs for majors from spec §5 (Grounding).

### 0.3. Keeping `AGENTS.md` in sync when `docs/` changes

`AGENTS.md` must remain the current entry point. **In the same task / session** where files under `docs/` change, the agent **must** review and update the corresponding sections of this file when needed:

| Change in `docs/` | Update in `AGENTS.md` |
|-------------------|------------------------|
| New / renamed ADR, Accepted status change | §2 (invariants / ADR refs), §0.2, §10 if stage-impact |
| New or shifted plan / Tasks / checkpoints | §10 (phase tables, stops, plan paths) |
| New or updated local SDD phase prompts / index / common prompt | §10.1, phase tables; if contract changes — §5–6 |
| Edits to `workflow.md` / `skills-map.md` / `role-prompts/` | §4–7, header links; anti-patterns §9 |
| New runbooks / substantial index edits | §0.2, §3, §10 if needed |
| Edits to `slo.md` | §0.2; quality/NFR wording in §2 if diverging from spec — spec first |
| Stage 2/3 plan/prompts appear | §10.3 / §10.4 |

Do not duplicate full ADR/runbook/prompt text here — only **navigation, invariants, and operational rules**. If `docs/` changed but `AGENTS.md` was not updated — the task is **not Done**.

---

## 1. Environment

- Work **locally only**: Docker Compose, uv, pytest, local git.
- **FORBIDDEN** without explicit human command: `git push`, `gh pr create` / remote mutations, deploy to staging/production, publishing images to a registry, live Keycloak-as-IdP, live Stripe.
- Commits — only when the human asks. Working tree may stay dirty.
- **NEVER** update git config; **NEVER** skip hooks (`--no-verify`) unless the human explicitly asks.
- CI (GitHub Actions): `make ci` — ruff, mypy, unit tests; sibling jobs `load-harness` (`make load-harness`: load group pytest + Locust `--list`, no stack) and `load-locust-smoke` (`cp .env.example .env`, `make stack-up`, `make load-locust`, always `make stack-down`; default smoke **without** `LOAD_LOCUST_OTEL=1`). Integration via `docker-compose.test.yml` / Makefile. Pre-commit: [`CONTRIBUTING.md`](CONTRIBUTING.md).
- Subagents: Cursor built-in models only (`composer-2.5`, `cursor-grok-4.5-high`; never BYOK; never `*-fast`); Implementer ≠ Reviewer.

### 1.1 Frozen host ports

| Service | Host port |
|---------|-----------|
| hub-api | 8000 |
| admin-ui | 8080 |
| postgres | 5432 |
| redis | 6379 |
| kafka | 9092 |
| otel-collector OTLP | 4317 (gRPC), 4318 (HTTP) |
| prometheus | 9090 |
| grafana | 3000 (credentials from env template) |
| jaeger | 16686 |
| partner-mock | 8090 |

Do not shuffle these. Do not publish random debug ports.

Local operator consoles (Compose demo only — not product ports):

| Service | Host port |
|---------|-----------|
| kafbat-ui (Kafka) | 8081 |
| redis-commander | 8082 |
| adminer (PostgreSQL) | 8083 |
| flower (Celery) | 8084 |

### 1.2 Compose project

Project name and default Docker network: `b2b-partner-integration-hub` (Makefile `-p` matches). Named volume: `b2b-partner-integration-hub-postgres-data`. Secrets and credentials live in gitignored `.env`; the tracked template is `.env.example`. Compose interpolates `POSTGRES_*` and `GF_SECURITY_ADMIN_*`. `HUB_REPLAY_APPROVAL_REQUIRED=true` is a Compose override on `hub-api` only (host pytest uses the Settings default `False`).

---

## 2. Invariants (Global Constraints)

From `spec.md` (architecture §4, identifiers §6.3, stack §5, NFR §8, tests §10, ADR summaries §12) and Accepted ADRs in `docs/adr/`:

1. **At-least-once** + partner idempotency. No exactly-once e2e — [ADR-001](docs/adr/001-at-least-once.md).
2. **Retries via Kafka retry topics**, not Celery countdown — [ADR-002](docs/adr/002-kafka-retry-topics.md). Celery = scheduled maintenance only (`replay_stale_failed`, `purge_old_idempotency_keys`, `rotate_webhook_secrets` notify).
3. **PostgreSQL SoT**; Kafka = bus. Client: **aiokafka** (async workers) — [ADR-003](docs/adr/003-postgres-sot-kafka-bus.md). Message key = partner **`public_id`** (partition affinity / order per partner) — spec §12.11.
4. **HMAC-SHA256** Stripe-style (`timestamp + "." + raw body`); `hmac.compare_digest`; timestamp skew 300s → 403 — [ADR-004](docs/adr/004-hmac-sha256.md).
5. **Circuit breaker per partner** in Redis — [ADR-005](docs/adr/005-circuit-breaker.md). Stage 2 Must. Redis down: **fail-open** outbound + DB UNIQUE for inbound idempotency.
6. **Thin admin UI** (Vite+React+TS): **no** retry/HMAC/outbox/CB logic in the browser — [ADR-006](docs/adr/006-thin-admin-ui.md). UI talks **only** to Admin API.
7. **Transactional outbox** Stage 2 mandatory — [ADR-007](docs/adr/007-transactional-outbox.md). Stage 1 historically allowed publish-after-commit with `hub_outbox_discrepancy_total` (now **retired** from the live catalog; ADR note only). Never silent dual-write after Stage 2.
8. **SLA clock** stops at `first_success_at` — [ADR-008](docs/adr/008-sla-compliance.md). Hub measures compliance; it does **not** compute money penalties. Replay does **not** rewrite payload (spec §12.12).
9. **Identifiers (ADR-009 / §6.3):** dual-id (`BIGINT` PK + `public_id` UUIDv7) **only** on `partners` and `deliveries`. Sequential `BIGINT id` **never** in DTO, OpenAPI, Admin UI, Kafka payloads, or replay APIs. No composite PK. Natural UNIQUE as spec.
10. **Poison taxonomy:** 408/429/5xx/network → retry; 400/401/403/404/422 → immediate `failed` + DLQ. Configurable `retry_on_status_codes` per endpoint (default transient only). After DLQ, consumer **commits offset**.
11. **Out of scope:** Partner Portal, SOAP/FTP/EDI, payload mapping plugins as Must, WAF/DDoS/mTLS as Must, multi-region AA, Hub usage billing, exactly-once, legal penalty math, Confluent cloud, Tempo, OIDC/Keycloak as Stage 1 Must, service mesh.
12. **Auth:** partner API keys (argon2 hash, prefix for lookup, plaintext shown **once**); HMAC on body. Admin: Stage 1 static token → JWT stub (HS256); RBAC `hub_admin` / `hub_operator` / `hub_viewer` (spec §2.2).
13. **Secrets at rest:** signing secrets Fernet; never log secrets, HMAC, or full API keys.
14. **Multi-tenant:** every query scoped by partner. No cross-partner leakage in logs. Log `delivery_id` / `partner_id` as **public** ids or slug.
15. **httpx:** connect/read timeouts from settings; **no** unbounded library retries on outbound POST — retries only via Kafka.
16. **Invalid state transition** → log + `hub_invalid_transition_total` (bug).
17. **Observability:** OTel SDK → Collector → Prometheus (metrics) + **Jaeger** (traces). Not Tempo. Metric attributes = `partner_slug` (not `partner_id` UUID), `status`, `event_type`, `http_status_class`, `reason`, `trigger`, `direction`, `topic`, `group`. Never `delivery_id`, `correlation_id`, `trace_id`, UUIDv7 as metric attributes.
18. **Correlation:** header `X-Correlation-Id` (accept `X-Correlation-ID`); value **UUIDv7**; invalid → **422**; echo on responses. Generate if missing on Internal/Admin/inbound.
19. **Pagination:** `limit` + `offset` only (default `limit=50`, max 200). Do not invent `cursor` in docs or OpenAPI unless the handler implements it.
20. **Public HTTP contract = live `/docs`**, not markdown catalogs. OpenAPI `info.title` = `Partner Integration Hub`.
21. **Quality gates:** ruff 0, mypy strict on `app/` and `celery_app/`, unit coverage ≥ 80% core (target ≥ 85% on `app/domain`, `app/api`, `app/workers`).
22. **Docs language:** everything in git is professional English (`spec.md` included). Do not markdown-link any path listed in `.gitignore`.
23. **Do not copy** neighboring product invariants: OFOM (no dual-id, gRPC mesh, saga, three `app` packages); billing (ledger, entitlements cache, Stripe port, org tenancy); SSO (OIDC BFF, Keycloak, PKCE). **Do copy process:** SDD, Implementer ≠ Reviewer, live `/docs` bar, git init-without-commit.
24. **Multi-URL fan-out (ADR-010 / Stage 3):** all active outbound endpoints whose `event_types` contain the event are delivered. Stored idempotency key is `{client_key}::{endpoint.public_id}`; UNIQUE `(partner_id, idempotency_key)` unchanged. Duplicate on `source_event_id` (caller key) is strict — no retroactive rows for endpoints added later.

---

## 3. Repository structure (target spec §9)

```
app/                    # single Python package: api, domain, workers, integrations
admin_ui/               # Vite + React + TypeScript thin SPA
celery_app/             # scheduled maintenance only (not webhook transport)
alembic/                # migrations (not create_all on Compose path)
infra/kafka|otel|prometheus|grafana/
docs/adr|plans|agentic|runbooks|openapi|asyncapi|grafana/
tests/unit|integration|contract|e2e
partner_mock/           # FastAPI mock for contracts + demo chaos
docker-compose.yml
docker-compose.test.yml
Makefile, pyproject.toml, README.md
```

Application packages appear starting Stage 1 Task 0 tooling — domain models from the models/Alembic task. Until then, verify docs/harness via filesystem inventory.

---

## 4. Skills / subagents

| Moment | Skill / Task | Details |
|--------|----------------|--------|
| Plan | `superpowers:writing-plans` | [`skills-map.md`](docs/agentic/skills-map.md); save under `docs/plans/`; **strip commit steps** |
| Product forks before code | `superpowers:brainstorming` | spec already decided — do not invent SOAP/Portal/mesh |
| Execute plan task-by-task | `superpowers:subagent-driven-development` | + [`role-prompts/orchestrator.md`](docs/agentic/role-prompts/orchestrator.md); **no** finishing-branch/PR |
| Implement Task N | — | `generalPurpose` (Implementer) |
| Review | Reviewer ≠ Implementer | [`role-prompts/reviewer.md`](docs/agentic/role-prompts/reviewer.md) |
| Security | HMAC, keys, Fernet, RBAC | [`role-prompts/security.md`](docs/agentic/role-prompts/security.md) |
| Grounding | stack §5 library docs | [`role-prompts/grounding.md`](docs/agentic/role-prompts/grounding.md) |
| Docs / ADR / sync AGENTS | Docs role | [`role-prompts/docs.md`](docs/agentic/role-prompts/docs.md) |
| Tests / pyramid | Test role | [`role-prompts/test.md`](docs/agentic/role-prompts/test.md) |
| Bug / red test | `superpowers:systematic-debugging` | — |
| Before “done” | `superpowers:verification-before-completion` | + §0.3 if `docs/` touched |
| Worktree | `superpowers:using-git-worktrees` | on human request |

---

## 5. Orchestration

1. Start from **this file** → active stage plan under `docs/plans/` (§0.1 / §10).
2. Per task — fresh Implementer with a self-contained prompt (Files, Interfaces, Spec §§, Acceptance, Global Constraints).
3. After implementation — a **separate** Reviewer (Gates A–D). Same agent must not APPROVE itself.
4. Parallel Task only when explicitly marked in plan + sync point; **never** two implementers on the same paths.
5. Stop-the-line: REQUEST CHANGES / red tests / grounding failure / security BLOCK → fix → re-review.
6. Between ordinary tasks within a phase, do not ask "continue?".
7. Human checkpoint / BLOCKED — stop until explicit human command. Do **not** declare Stage N Done; write evidence files.
8. Changed `docs/` → update `AGENTS.md` (§0.3) before declaring the task complete.

---

## 6. Review Gates (every task)

| Gate | This product |
|------|----------------|
| **A Spec/ADR** | Dual-id boundary; at-least-once; Kafka retries not Celery; HMAC algorithm; poison taxonomy; outbox rule for current stage; thin UI; SLA clock on `first_success_at`; message key = partner public_id |
| **B Quality** | SQLAlchemy 2 async no lazy-load; layout spec §9; ruff/mypy; TDD evidence; OpenAPI tests if HTTP touched |
| **C Security/ops** | Secrets not in git; keys hashed; secrets redacted in logs; no high-card OTel metric attributes; OTLP to Collector (not vendor SDK); health/ready |
| **D Failure** | Dual-write attempt blocked (S2); HMAC fail 403; duplicate idempotency 200; 400→DLQ; 503→retry; invalid transition metric; Redis down fallback documented |

---

## 7. Docs-grounding (libraries) and project `docs/`

- **Project `docs/`** — read per §0; do not ignore ADR/runbooks/slo in favor of "code only".
- **Official library docs** (Grounding) required for stack §5 patterns: FastAPI metadata/OpenAPI, Pydantic v2, SQLAlchemy 2 asyncio, Alembic, httpx timeouts, aiokafka, Redis, Celery vs Kafka, HMAC `compare_digest`, OpenTelemetry Python + Collector, Prometheus OTLP, W3C Trace Context, Jaeger native OTLP.
- Spec↔library docs conflict: product invariants from spec/ADR; library APIs from current docs; trade-off → ADR amendment + update §2 / §0.2 here if needed.
- Never cite `spec.md`, ADR filenames, runbooks, this file, or gitignored paths **inside OpenAPI prose**. Restate invariants in operator language.

---

## 8. Local commands

```bash
cp .env.example .env
uv sync
uv sync --group load     # Locust harness (host-side only)
make compose-up          # or: make stack-up (alias, --build --wait; 1 process per service)
make perf-up            # non-default overlay: uvicorn --workers 4, hub-api CPU 4.0, 2× worker, 2× relay; characterization only — not spec §8.1 proof
make migrate            # host-side Alembic; Compose runs hub-migrate before app services
make seed && make seed-prod-like
make ci                 # lint + typecheck + test-unit
uv run pre-commit install
uv run pre-commit run --all-files   # or --files <paths> when untracked (see CONTRIBUTING)
make load-harness       # load group pytest + Locust --list (no stack; matches GHA load-harness)
make export-openapi
# Load smoke (full stack must be up; export .env in shell — scripts do not source it):
set -a && source .env && set +a && make load-locust
make load-k6            # k6 persist-path regression (see docs/perf/)
make load-locust-otel   # opt-in Locust --otel → Collector :4318 (see docs/perf/locust-otel-grafana.md)
make load-k6-grafana    # opt-in k6 Prometheus RW on compose network (dashboard 19665); constant VUs, not ramping-arrival-rate
make stack-down         # tear down Compose including docker-compose.perf.yml (--remove-orphans; no -v)
```

Live contracts: `http://localhost:8000/docs`, Grafana `:3000`, Jaeger `:16686`, admin UI `:8080`. Operator consoles: Kafka UI `:8081`, Redis Commander `:8082`, Adminer `:8083`, Flower `:8084`.

---

## 9. Anti-patterns

- Start development / subagent dispatch without this file
- One agent writes and APPROVEs itself
- Placeholder "TBD" / "add tests" / "same as Task N"
- Markdown API catalogs instead of live `/docs`; empty FastAPI title; `docs_url=None`
- `app.mount("/metrics")`; dual `prometheus-client` + OTel for the same series; Tempo
- Sequential `id` in JSON/OpenAPI/UI/Kafka for dual-id entities
- Dual-write without outbox after Stage 2; Celery as webhook transport; exactly-once claims
- OIDC/Keycloak as Stage 1 Must; copy-paste OFOM no-dual-id, billing ledger, SSO PKCE
- UUID / `delivery_id` / `trace_id` Prometheus labels
- UUIDv4 correlation accepted / 500 on v4
- Business logic in React; replay that mutates payload; blocking Kafka partition on poison
- `create_all` instead of Alembic in Compose; root user in image; secrets in git
- Inventing SOAP/Portal/mesh; rewriting `spec.md` into `TZ.md`
- Commits or pushes without human command; declaring Stage Done without evidence file
- Linking any path listed in `.gitignore` (IDE internals, `.env`, caches, built UI) from tracked markdown
- Change `docs/` without updating corresponding `AGENTS.md` sections (§0.3)
- `composer-2.5-fast` / BYOK subagents

---

## 10. Stage development (supplement)

### 10.1. Common entry

| Artifact | Path |
|----------|------|
| Workflow | [`docs/agentic/workflow.md`](docs/agentic/workflow.md) |
| Role prompts | [`docs/agentic/role-prompts/`](docs/agentic/role-prompts/) |
| Phase prompts (local) | gitignored SDD harness (see §10.1) |
| Skills map | [`docs/agentic/skills-map.md`](docs/agentic/skills-map.md) |
| Progress / DoD evidence | `.superpowers/sdd/progress.md` (gitignored local harness) |
| ADR | [`docs/adr/`](docs/adr/) |
| Runbooks | [`docs/runbooks/`](docs/runbooks/) |
| SLI/SLO | [`docs/slo.md`](docs/slo.md) |
| Architecture | [`docs/architecture.md`](docs/architecture.md) |

Read [`docs/agentic/workflow.md`](docs/agentic/workflow.md) and the active stage plan before any phase work.

### 10.2. Stage 1 — MVP (`spec.md` §3.3 / §11.3)

**Plan:** [`docs/plans/2026-06-01-stage1-implementation-plan.md`](docs/plans/2026-06-01-stage1-implementation-plan.md)

| Phase | Tasks | Focus |
|-------|-------|--------|
| P0 | 0 | uv, Makefile, `.env.example`, README skeleton, OpenAPI test lock |
| P1 | 1–2 | Compose data plane + partner-mock + OTel Collector + Prometheus + Jaeger + Grafana |
| P2 | 3–6 | Settings/OTel/logs; HMAC; backoff/SM/SLA; dual-id models + Alembic |
| P3 | 7–11 | FastAPI `/docs` bar; partners; inbound; internal outbound; admin replay |
| P4 | 12–13 | Outbound worker + retry.30s + DLQ; fault-injection subset |
| P5 | 14 | Thin admin UI |
| P6–P7 | 15–16 | Grafana + seed; contract tests + CI + Stage 1 evidence |

**Stage 1 Must:** inbound+outbound, one retry tier `hub.outbound.retry.30s`, DLQ, audited single replay, thin UI list/detail/replay, OTel metrics+HTTP traces, full OpenAPI, `make seed`. Outbox relay, CB, retry tiers, Celery beat, bulk replay, secret rotation table — **Stage 2**.

### 10.3. Stage 2 — Industrial (`spec.md` §3.4)

**Plan:** [`docs/plans/2026-06-02-stage2-implementation-plan.md`](docs/plans/2026-06-02-stage2-implementation-plan.md)

Retry tiers + jitter; Redis CB; `outbox_events` + `hub-outbox-relay`; httpx pool; Celery beat; rate limits; bulk replay; DLQ ack/purge; secret rotation overlap; compliance Grafana; AsyncAPI CI; partner summary API.

### 10.4. Stage 3 — Enterprise (`spec.md` §3.5)

**Plan:** [`docs/plans/2026-06-04-stage3-implementation-plan.md`](docs/plans/2026-06-04-stage3-implementation-plan.md)

Multi-URL + `event_type` routing; JSON Schema registry stub; replay approval; `GET /partner/v1/deliveries/{id}`; k6 vs documented p95; W3C on Kafka; HA Kafka **docs** (Compose may stay 1 broker); weekly compliance export.

**Out of Must:** mesh, OIDC, multi-region, Confluent cloud, WAF, Partner Portal, Helm/kind (optional; if added must not be toy YAML).

### 10.5. Ad-hoc — stack versions + full audit + load/perf

Local gitignored harness (not Stage N; no Stage Done; no commit unless asked):

- `PROMPT_STACK_VERSION_UPGRADE.md` — current stable FastAPI/Celery/Redis + **Kafka 4.x** with aiokafka (keep OTel 1.44); then Phase A of full audit
- `PROMPT_FULL_PROJECT_AUDIT_AND_DOCS.md` — live seed audit + EN operator docs
- Locust + k6 Grafana + CI + prod-like ceiling: Wave 0 inventory in local SDD harness; **Wave 1** [`docs/plans/2026-06-10-locust-load-testing.md`](docs/plans/2026-06-10-locust-load-testing.md) (Tasks 1–3 APPROVE). **Wave 2** [`docs/plans/2026-06-10-locust-k6-grafana.md`](docs/plans/2026-06-10-locust-k6-grafana.md) (Locust `--otel` via existing Collector :4318; k6 Prometheus RW; Grafana dashboards `locust-otel.json` + k6 **19665**). **Wave 3** [`docs/plans/2026-06-08-ci-pre-commit.md`](docs/plans/2026-06-08-ci-pre-commit.md) — GHA `load-harness` / `load-locust-smoke`; tracked `.pre-commit-config.yaml` (Ruff **0.5.7**, file hooks, local `import loadtests.locustfile`; no Docker in hooks); install via [`CONTRIBUTING.md`](CONTRIBUTING.md). **Wave 4** [`docs/plans/2026-06-13-ceiling-hunt.md`](docs/plans/2026-06-13-ceiling-hunt.md) — `make perf-up` overlay + live ceiling hunt (not spec §8.1); named limiter API/process — [`docs/perf/ceiling-prodlike.md`](docs/perf/ceiling-prodlike.md). **Wave 5** [`docs/plans/2026-06-12-api-process-opt.md`](docs/plans/2026-06-12-api-process-opt.md) — validator reuse + sessionmaker cache + overlay CPU 4.0 (not §8.1 proof). **Wave 6** [`docs/plans/2026-06-14-remeasure.md`](docs/plans/2026-06-14-remeasure.md) — rebuild + remesure same overlay; limiter still API/process at 4.0 — [`docs/perf/ceiling-remeasure.md`](docs/perf/ceiling-remeasure.md). **Wave 7** [`docs/plans/2026-06-12-accept-path-opt.md`](docs/plans/2026-06-12-accept-path-opt.md) — pure ASGI middleware + accept-path L1 cache; remesure same overlay; limiter **DB/pool (accept-path writes)** — [`docs/perf/ceiling-accept-path.md`](docs/perf/ceiling-accept-path.md). **Wave 8** [`docs/plans/2026-06-12-accept-path-db-opt.md`](docs/plans/2026-06-12-accept-path-db-opt.md) — insert-first idempotency + `pool_pre_ping=False`; remesure same overlay; limiter **API/process** at 4.0 — [`docs/perf/ceiling-db-roundtrip.md`](docs/perf/ceiling-db-roundtrip.md). Persist CTE remesure (same overlay, rebuilt images) — limiter still **API/process** at 4.0 — [`docs/plans/2026-06-13-persist-cte-remeasure.md`](docs/plans/2026-06-13-persist-cte-remeasure.md), [`docs/perf/ceiling-persist-cte.md`](docs/perf/ceiling-persist-cte.md). Isolated Kafka pending drain (lag=0 before each hold; ≈69 msg/s after Locust stop) — [`docs/plans/2026-06-13-kafka-lag-drain-remeasure.md`](docs/plans/2026-06-13-kafka-lag-drain-remeasure.md), [`docs/perf/ceiling-kafka-lag-drain.md`](docs/perf/ceiling-kafka-lag-drain.md). Existing k6 (`load/k6/`, `make load-k6`) stays; Locust is additive (`make load-locust`, `make load-locust-ui`, `make load-locust-otel`, `make load-k6-grafana`, `make load-harness`, `make perf-up`). Runbook: [`docs/runbooks/load-testing.md`](docs/runbooks/load-testing.md). Evidence: [`docs/perf/locust-smoke.md`](docs/perf/locust-smoke.md), [`docs/perf/locust-otel-grafana.md`](docs/perf/locust-otel-grafana.md), [`docs/perf/ceiling-prodlike.md`](docs/perf/ceiling-prodlike.md), [`docs/perf/ceiling-remeasure.md`](docs/perf/ceiling-remeasure.md), [`docs/perf/ceiling-accept-path.md`](docs/perf/ceiling-accept-path.md), [`docs/perf/ceiling-db-roundtrip.md`](docs/perf/ceiling-db-roundtrip.md), [`docs/perf/ceiling-persist-cte.md`](docs/perf/ceiling-persist-cte.md), [`docs/perf/ceiling-kafka-lag-drain.md`](docs/perf/ceiling-kafka-lag-drain.md).

Always attach `PROMPT_COMMON.md`. Subagents: `composer-2.5` or `cursor-grok-4.5-high`; Implementer ≠ Reviewer.
