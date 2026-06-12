# Runbook: Replay procedure

**Spec:** §7.1.3, §12.12

## When

A delivery is `failed` (and usually has a dead-letter row) after partner recovery or a fixed contract.

## Procedure

1. Open the delivery by **public** id (`GET /admin/v1/deliveries/{id}`).
2. Confirm payload must stay **unchanged**. Editing payload is a new source event, not replay.
3. `POST /admin/v1/deliveries/{id}/replay` with required `reason`. Optional `reset_attempt_counter`.
4. Role: `hub_operator` or `hub_admin`. `hub_viewer` cannot replay.
5. Confirm `audit_logs` row (`delivery.replay`) and UI status `replaying` → `delivered` or `failed`.

## Bulk (Stage 2)

`POST /admin/v1/deliveries/bulk-replay` with `reason`. Respect partner rate limit and open circuits. Never bulk-replay poison 4xx without a contract fix.
