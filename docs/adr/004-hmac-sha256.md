# ADR-004: HMAC-SHA256 (Stripe-style) plus API key

- **Status:** Accepted
- **Date:** 2026-06-01
- **Spec:** §7.1.1, §7.5, §12.4

## Context

Inbound and outbound webhooks need authenticity and integrity. Partners already know Stripe-style signatures. mTLS is a perimeter concern, not a replacement for payload signatures.

## Decision

Algorithm (inbound and outbound):

1. Read **raw body bytes** (never re-serialized JSON).
2. Reject if `|now - timestamp| > 300` seconds → **403**.
3. `signed_payload = f"{timestamp}.".encode() + body`
4. HMAC-SHA256 with `primary` secret; Stage 2 also tries `previous` inside the rotation window.
5. Compare with `hmac.compare_digest` against the header hex after stripping the `sha256=` prefix. Fail → **403**.
6. Inbound also requires `Authorization: Bearer <api_key>` (prefix lookup, argon2 verify). Fail → **401**.

No Stripe SDK. Secrets at rest: Fernet. Stage 1 may store one encrypted `signing_secret` on `partners`; Stage 2 migrates to `partner_signing_secrets`.

## Consequences

- Clock skew beyond 300s looks like a bad signature (403), not 401.
- Tests must use raw bytes, not `json.dumps` round-trips with different separators.

## Alternatives considered

- mTLS only — not partner-portable; optional later at the edge.
- JWT in the body — couples auth to payload schema.
- Non-constant-time compare — timing leak.

## Links

- Spec §12.4
- ADR-009 (public ids on outbound headers)
