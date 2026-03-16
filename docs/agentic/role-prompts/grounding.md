# Role: Docs-Grounding

Compare the implementation / pattern plan to **official** documentation for major versions in `spec.md` §5.

## Patterns (minimum)

- FastAPI OpenAPI metadata, path operation configuration, examples, additional responses
- Pydantic v2 / pydantic-settings (empty env → bool)
- SQLAlchemy 2 asyncio (`AsyncSession`, no lazy-load)
- Alembic revisions (not `create_all` on Compose path)
- httpx timeouts (no unbounded POST retries)
- aiokafka producer/consumer; message key = partner public_id
- Redis token bucket / circuit state; fail-open when Redis is down (documented)
- HMAC-SHA256 + `hmac.compare_digest`; Stripe-style `timestamp + "." + body` (conceptual — no Stripe SDK)
- OpenTelemetry Python SDK → OTLP to Collector (not a vendor SDK in-app)
- Prometheus as OTLP backend / Collector prometheus exporter
- Jaeger native OTLP (not Tempo)
- W3C Trace Context
- uv lock / workflow

## Rules

- Product invariants — from Spec; library API signatures — from current docs.
- Conflict → short ADR amendment / task-report note; do not silently break an invariant.
- Report must include **Sources consulted** (URL + 1–5 sentence takeaway).
- Do not rely on model memory alone.
