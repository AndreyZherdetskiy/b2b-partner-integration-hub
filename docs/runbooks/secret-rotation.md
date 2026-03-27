# Runbook: Signing secret rotation

**Endpoint:** `POST /admin/v1/partners/{id}/rotate-secret`
**Role:** `hub_admin`
**Spec:** §7.1.2, ADR-004

`{id}` is the partner **public UUIDv7** (`public_id`), not the internal sequential id.

## When

- Scheduled rotation policy or credential compromise response
- Partner onboarding handoff after initial secret share
- Before revoking a partner that still receives inbound traffic

## Procedure

1. Confirm `FERNET_KEY` is configured on `hub-api` (rotation returns 422 if missing).
2. `POST /admin/v1/partners/{id}/rotate-secret` with `hub_admin` JWT.
3. Response includes the new `signing_secret` plaintext **once** — store out-of-band; it is not retrievable later.
4. Previous secret remains valid for **inbound** HMAC verification until `valid_until` on the prior `partner_signing_secrets` row.
5. Overlap window: `hub_secret_rotation_overlap_hours` (default **24h**). Partners should accept both signatures during overlap.
6. **Outbound** hub POSTs always sign with the **primary** secret only.
7. Verify `audit_logs` row: action `signing_secret.rotate`, `resource_id` = partner public UUID.

## Checks after rotation

- Partner inbound: send test event with new secret → 202; old secret still works until `valid_until`.
- Partner outbound: hub signatures use new primary immediately after rotation.
- Admin UI partner detail reflects rotation timestamp if exposed.

## Safe actions

- Rotate during a maintenance window when possible; communicate overlap end time to the partner.
- Re-run rotation if the one-time plaintext was lost (generates another primary; previous primary becomes overlap secret).

## Do not

- Share the plaintext secret in tickets, chat, or logs.
- Expect outbound deliveries to sign with the overlap (previous) secret.
- Rotate without Fernet encryption configured — secrets at rest must stay encrypted.

## Escalation

If inbound 403 spikes after rotation, partner may still be using an expired previous secret or wrong body canonicalization. See partner onboarding runbook for HMAC on **raw body** + timestamp.
