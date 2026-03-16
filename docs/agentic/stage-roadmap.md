# Stage roadmap — Partner Integration Hub

Product stages: [`spec.md`](../../spec.md) §3.3–3.5, §11. Agent entry: [`AGENTS.md`](../../AGENTS.md) §10.
**Stage1 plan:** [`docs/plans/2026-03-14-stage1-implementation-plan.md`](../plans/2026-03-14-stage1-implementation-plan.md).

## Stage 1 — MVP (spec §3.3)

| Phase | Focus | Tasks |
|-------|-------|--------|
| P0 | Bootstrap uv / Makefile / env / OpenAPI lock | 0 |
| P1 | Compose data plane + OTel + partner-mock | 1–2 |
| P2 | Domain units + dual-id Alembic | 3–6 |
| P3 | hub-api inbound / internal / admin + `/docs` | 7–11 |
| P4 | Outbound worker + DLQ + fault injection | 12–13 |
| P5 | Thin admin UI | 14 |
| P6–P7 | Grafana + seed + CI + evidence | 15–16 |

**Must:** partners/endpoints; outbound pending + `retry.30s`; DLQ; inbound HMAC+idempotency; single replay + audit; thin UI; OTel → Collector → Prometheus + Jaeger; live `/docs`.
**Not Must:** outbox relay, circuit breaker, retry tiers, Celery beat, bulk replay, secret rotation history table.

## Stage 2 — Industrial (spec §3.4)

**Plan:** [`docs/plans/2026-03-27-stage2-implementation-plan.md`](../plans/2026-03-27-stage2-implementation-plan.md)

Retry tiers `1m`/`5m`/`15m`/`1h` + jitter; Redis CB; transactional outbox + `hub-outbox-relay`; rate limits; Celery scheduled replay; bulk replay; DLQ ack/purge; signing secret overlap; compliance Grafana + alerts; AsyncAPI CI; partner summary API; UI filters/bulk/compliance.

**Prove:** Kafka down after delivery insert → unpublished row → relay catch-up.

## Stage 3 — Enterprise (spec §3.5)

**Plan:** [`docs/plans/2026-03-28-stage3-implementation-plan.md`](../plans/2026-03-28-stage3-implementation-plan.md)

Multi-URL + `event_type` routing; JSON Schema registry stub (PG); replay approval; `GET /partner/v1/deliveries/{id}`; k6 vs documented p95; W3C `traceparent` on Kafka; HA Kafka **documented** (Compose may stay 1 broker, RF=1); weekly compliance export; runbooks complete.

**Out of Must:** mesh, OIDC, multi-region, Confluent cloud, WAF, Partner Portal. Helm/kind optional — if added: requests/limits, probes, secrets from env, not toy YAML.

## Human-owned labels

Write `.superpowers/sdd/progress.md` “implementer complete / reviewer APPROVE”. **Do not** announce “Stage N Done”. Evidence: local gitignored Stage N DoD evidence file.
