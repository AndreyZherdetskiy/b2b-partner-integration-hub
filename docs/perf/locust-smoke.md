# Locust smoke (accept path)

## What this measures

Headless Locust against the **full** Compose stack (`hub-api` on `:8000`). Success on outbound = HTTP **202** (`status: accepted`). This is an operator smoke of the ingest accept path, **not**:

- partner webhook delivery time or `delivered` state
- spec §8.1 NFR / contractual throughput
- a substitute for k6 persist-path regression ([`outbound-ingest.md`](./outbound-ingest.md))

## Prerequisites

Same as [`docs/runbooks/load-testing.md`](../runbooks/load-testing.md): `make stack-up`, `make seed`, export env (`set -a && source .env && set +a`).

## Fail-closed (no credentials)

Command:

```bash
env -u ADMIN_BOOTSTRAP_TOKEN -u LOAD_ADMIN_TOKEN ./scripts/load_smoke.sh
```

Observed stderr (2026-03-31):

```text
preflight failed: ADMIN_BOOTSTRAP_TOKEN or LOAD_ADMIN_TOKEN must be set in process environment
```

Exit code: **1**. Locust did not start; **0** HTTP requests recorded.

## Successful smoke

Command:

```bash
set -a && source .env && set +a
make load-locust
```

| Parameter | Value |
|-----------|-------|
| Date | 2026-03-31 |
| Overlay | Default `docker-compose.yml` (not `docker-compose.perf.yml`) |
| `LOAD_HOST` | `http://127.0.0.1:8000` |
| Users | 2 |
| Spawn rate | 1/s |
| Run time | 10s |
| Exit code | 0 |

Artifacts: `.local/locust/smoke.html`, `.local/locust/smoke_stats.csv` (gitignored).

### Results (`smoke_stats.csv`)

| Name | # reqs | RPS | Fail % | p50 (ms) | p99 (ms) |
|------|--------|-----|--------|----------|----------|
| `GET /inbound/v1/health` | 11 | 1.27 | 0 | 3 | 5 |
| `POST /internal/v1/outbound/events` | 38 | 4.39 | 0 | 14 | 99 |
| **Aggregated** | **49** | **5.66** | **0** | **13** | **99** |

Both named endpoints appear in stats. All outbound responses were HTTP 202.

## Locust web UI (optional)

Host port **8089** was free before the smoke run. UI check was not left running for this evidence capture; use `make load-locust-ui` when you need the browser UI (script fails closed if `:8089` is busy).

## Related

- Runbook: [`docs/runbooks/load-testing.md`](../runbooks/load-testing.md)
- k6 persist regression: [`outbound-ingest.md`](./outbound-ingest.md)
