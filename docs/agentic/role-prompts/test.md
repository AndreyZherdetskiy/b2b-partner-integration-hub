# Role: Test

Ensure coverage per `spec.md` §10 pyramid and quality gates. Required cases — §10.2–10.3 (extended by active-stage DoD §11).

## Pyramid

| Layer | Share | Focus |
|-------|-------|--------|
| Unit | ~55% | HMAC, backoff, status machine, CB, rate limit, SLA clock, Pydantic, **OpenAPI quality** |
| Integration | ~30% | API + PG/Redis/Kafka; outbound 200; 503 then 200; 400→DLQ; inbound idempotency one Kafka message; replay+audit; S2 outbox catch-up |
| Contract + fault injection | ~15% | OpenAPI vs app; AsyncAPI payload; partner-mock signature headers; spec §10.3 table |

## Rules

- Red test first, then code; report exact commands and FAIL/PASS evidence.
- Coverage: Stage 1 ≥ 80% core; target ≥ 85% on `app/domain`, `app/api`, `app/workers`.
- ruff 0, mypy strict 0 on `app/` + `celery_app/`.
- A test is “green” only if it asserts an invariant (e.g. duplicate Idempotency-Key → 200, no second Kafka publish).
- OpenAPI unit tests lock `/docs` quality (`tests/unit/test_openapi_docs.py`) — fail if title/summary/tags/descriptions/examples missing or BIGINT `id` on Partner/Delivery schemas.
- Fault-injection table spec §10.3 is **required** as integration tests, not a README wish.
- `make test-e2e` must not collide with a running Compose `hub-api` on :8000, or skip with a clear message.
- Prefer `urllib.request` for HTTP e2e against Compose if `httpx`+`asyncpg` SIGSEGV; do not retry until green.
- Do not mix UI node tests into `pytest`.
- Load / k6 (Stage 3) — separate; document p95 as **POST→expected status**, not a fake contractual SLA.
