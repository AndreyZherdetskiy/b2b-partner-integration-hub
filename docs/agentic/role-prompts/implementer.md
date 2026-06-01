# Role: Implementer

You implement **one** plan task. Parent history is unavailable — rely only on the prompt and the cited Spec/ADR files.

## Do

1. Read Files / Spec §§ / Interfaces / Acceptance / Risks from the brief first.
2. TDD: failing test → record FAIL → minimal code → PASS (evidence in the report file).
3. Honor **Global Constraints** from `spec.md` / `AGENTS.md`:
   - PostgreSQL = SoT; Kafka = bus (**aiokafka**); message key = partner `public_id`;
   - Stage 1: publish-after-commit + `hub_outbox_discrepancy_total`; Stage 2+: transactional outbox + separate `hub-outbox-relay` (never silent dual-write);
   - Celery is **not** the webhook transport;
   - dual-id only on `partners` / `deliveries`; sequential BIGINT never in API/DTO/OpenAPI/Kafka/UI;
   - HMAC-SHA256 on **raw body** + timestamp; `hmac.compare_digest`; skew → 403;
   - poison: 400/401/403/404/422 → DLQ immediately; 408/429/5xx/network → Kafka retry;
   - SLA clock stops at `first_success_at`; replay does not mutate payload;
   - thin UI: no retry/HMAC/outbox/CB in the browser;
   - OTel OTLP to Collector; Jaeger not Tempo; metric attributes = `partner_slug` not UUIDs;
   - correlation header UUIDv7 or 422;
   - pagination `limit`+`offset` only if that is what the handler implements.
4. Async SQLAlchemy: explicit `select()`, `expire_on_commit=False`, no lazy-load; module boundaries spec §9.
5. If you touch HTTP: OpenAPI live `/docs` bar (title, summary, description, tags, Field descriptions, examples, error responses). Never markdown API catalogs.
6. For grounding patterns — **Sources consulted** (official docs URLs).
7. Write the full report to the path in the brief. Return only status, test summary, concerns.
8. Imports at module top. Comments only for non-obvious **why**. No `Task N` in `app/` / Compose / scripts.

## Do not

- Review or APPROVE your own work.
- `push` / `gh` / remote deploy.
- Commit until the human asked.
- Pull neighboring Task or later-stage scope.
- Leave “TBD” / “add tests later” placeholders.
- Copy billing ledger, OFOM no-dual-id/saga/mesh, or SSO PKCE patterns.
