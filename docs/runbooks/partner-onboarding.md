# Runbook: Partner onboarding

**Spec:** Appendix B, journey J1

1. `POST /admin/v1/partners` — slug, name, `sla_seconds`, rate limit.
2. Create API key (`POST .../api-keys`) — plaintext shown **once**; store out-of-band.
3. Signing secret is generated encrypted at rest; share out-of-band.
4. `POST .../endpoints` — outbound HTTPS URL, `event_types` (`order.created`, `order.updated`), timeouts, `max_attempts`.
5. Sandbox: `POST /admin/v1/deliveries/test` or Admin UI test send against `partner-mock` / partner sandbox.
6. Partner verifies `X-Hub-Signature-256`.
7. Inbound test: valid HMAC + `Idempotency-Key` → 202; duplicate → 200.
8. Set partner `status=active`. Subscribe to success-rate / DLQ / SLA alerts.

Canonical demo slugs (do not rename): `acme-erp`, `flaky-logistics`, `strict-payments`, `slow-crm`.
