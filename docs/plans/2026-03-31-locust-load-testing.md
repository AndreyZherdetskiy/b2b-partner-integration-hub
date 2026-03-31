# Locust load-testing harness (Wave 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed Locust smoke harness for Partner Integration Hub accept-path (`POST /internal/v1/outbound/events` → HTTP 202) without replacing Stage 3 k6 and without claiming spec §8.1 stand NFR from a laptop.

**Architecture:** New host-side package `loadtests/` (uv extra group `load`). Credentials and partner id come from **process environment** (no pydantic `.env` leak for the admin token). Preflight: token present → `GET /inbound/v1/health` 200 → resolve `acme-erp` public id. Locust `HttpUser` weights outbound accept vs health so both named tasks appear. Live smoke uses the **full** Compose stack (`make stack-up` alias of `compose-up`) plus `make seed`. Grafana OTEL / k6 remote-write / CI jobs / perf overlay are later waves.

**Tech Stack:** Python 3.12, uv group `load` (`locust[otel]>=2.32,<3`), httpx (already a project dep), pytest, Make, Compose project `b2b-partner-integration-hub`. Existing `grafana/k6` + `load/k6/outbound_ingest.js` stay.

## Global Constraints

- Product SoT: `spec.md` v3.1 EN + Accepted ADR 001–010 + `AGENTS.md`. Notify/billing Locust is a **pattern**, not source of truth.
- **Do not commit** (`git commit` / push / gh). Human owns commits. Do not declare Stage Done.
- **Locust is additive.** Do not delete, retune, or replace `load/k6/outbound_ingest.js`, `make load-k6`, or `docs/perf/outbound-ingest.md` numbers with Locust smoke. Locust ≠ proof of spec §8.1 100/500/2000 req/s or 2M deliveries/day.
- Auth for outbound ingest: Bearer **`ADMIN_BOOTSTRAP_TOKEN`** (optional override `LOAD_ADMIN_TOKEN`). Fail-closed = missing process-env token. Do **not** invent partner HMAC/API-key tasks in Wave 1 (inbound argon2 is a later-wave audit path).
- Health equivalent: **`GET /inbound/v1/health`** HTTP 200 (Compose `hub-api` healthcheck). There is no `/ready`.
- Accept contract: `POST /internal/v1/outbound/events` with JSON `partner_id` (public UUIDv7), `event_type` `order.created`, unique `idempotency_key`, UUIDv7 `correlation_id` (body and/or `X-Correlation-Id`) → **HTTP 202** and `status: accepted`. Duplicate key → 200 (do not use duplicate keys in smoke).
- Partner resolve: `LOAD_PARTNER_PUBLIC_ID` if set; else `GET /admin/v1/partners?limit=50&offset=0` with Bearer token, find `items[].slug == LOAD_PARTNER_SLUG` (default `acme-erp`), use `items[].id` (public UUIDv7 — never sequential BIGINT).
- Frozen host ports: do not rename/shift (AGENTS §1.1). Locust UI uses **8089**. Prometheus 9090 stays published.
- Compose project / network: `b2b-partner-integration-hub`. App DNS: `hub-api`, `postgres`, `redis`, `kafka`, `prometheus`, `grafana`, `hub-outbound-worker`, `hub-outbox-relay`, `partner-mock`.
- Default stack is **1×** process per service. Do **not** add `docker-compose.perf.yml` in Wave 1.
- Docs language: tracked markdown **English**. No `Task N` leftovers in `app/`, Compose, or scripts after the task.
- Tear-down (Task 3 only): `make stack-down` → `docker compose -p b2b-partner-integration-hub -f docker-compose.yml down --remove-orphans` (**no** `-v`, no prune). If the implementer started the stack, they must leave it down.
- Scripts **must not** `source .env`. Operator exports in the shell: `set -a && source .env && set +a`. Fail-closed proof uses `env -u ADMIN_BOOTSTRAP_TOKEN -u LOAD_ADMIN_TOKEN`.
- Official Locust: https://docs.locust.io/en/stable/writing-a-locustfile.html , https://docs.locust.io/en/stable/running-without-web-ui.html (`--headless`, `-u`, `-r`, `-t`, `--exit-code-on-error`, `--html`, `--csv`, `environment.process_exit_code`), https://docs.locust.io/en/stable/configuration.html , https://docs.locust.io/en/stable/telemetry.html (`locust[otel]` + `--otel` — **do not enable `--otel` in default smoke**).
- Cursor plugins: pytest TDD for helper tests. Do not change API/domain/worker code in Wave 1.
- `uv.lock` ruff is **0.5.7**. Pre-commit already pins `ruff` hook id at `v0.5.7` — do not change pre-commit in Wave 1.
- Quality gates: ruff 0 on touched paths; mypy strict stays on `app/`, `partner_mock/`, `celery_app/` only (`loadtests/` not added to mypy packages).
- Implementer ≠ Reviewer. No self-APPROVE.

## Git vs gitignore

| Tracked (git) | Ignored |
|---------------|---------|
| `loadtests/`, `scripts/load_smoke.sh`, `scripts/load_locust_ui.sh`, Makefile, pyproject.toml, uv.lock, `.env.example` | `.env`, `.venv/`, `.local/` (Locust HTML/CSV — already in `.gitignore`) |
| `docs/plans/2026-03-31-locust-load-testing.md`, `docs/perf/locust-smoke.md`, `docs/runbooks/load-testing.md`, `docs/perf/README.md`, `AGENTS.md`, `README.md`, `CONTRIBUTING.md` | `.superpowers/` (briefs, reports, ledger) |

---

## File map

| Path | Role |
|------|------|
| `loadtests/__init__.py` | Package marker |
| `loadtests/config.py` | Host, admin token, slug, wait bounds from process env |
| `loadtests/preflight.py` | Credentials + health + partner resolve + zero-request helper (no Locust import) |
| `loadtests/locustfile.py` | Task 2: HttpUser tasks |
| `tests/unit/test_load_helpers.py` | Task 1 pin tests |
| `scripts/load_smoke.sh` | Task 2 headless runner |
| `scripts/load_locust_ui.sh` | Task 2 UI + `:8089` fail-closed |
| `Makefile` | `stack-up`/`stack-down` aliases; `load-locust`, `load-locust-ui` |
| `pyproject.toml` / `uv.lock` | group `load`; ruff `src` includes `loadtests` |
| `.env.example` | Comment knobs only |
| `docs/runbooks/load-testing.md`, `docs/perf/locust-smoke.md` | Task 3 |

---

### Task 1: loadtests helpers + unit tests

**Status:** complete — review APPROVE (2026-03-31). No commit. Minors: untested `main()`, packaging/mypy deferred.

**Files:**
- Create: `loadtests/__init__.py`
- Create: `loadtests/config.py`
- Create: `loadtests/preflight.py`
- Create: `tests/unit/test_load_helpers.py`
- Modify: `pyproject.toml` — add `loadtests` to `[tool.ruff] src` only (do **not** add the `load` group yet; that is Task 2)

**Interfaces:**
- Consumes: httpx already in project dependencies; pytest from `dev`
- Produces (exact names):
  - `DEFAULT_LOAD_HOST = "http://127.0.0.1:8000"`
  - `DEFAULT_PARTNER_SLUG = "acme-erp"`
  - `load_host() -> str` — `LOAD_HOST` else `BASE_URL` else default; rstrip `/`
  - `admin_token() -> str` — `LOAD_ADMIN_TOKEN` else `ADMIN_BOOTSTRAP_TOKEN` from **`os.environ` only**; empty string if unset
  - `partner_slug() -> str` — `LOAD_PARTNER_SLUG` or `acme-erp`
  - `partner_public_id_from_env() -> str | None` — `LOAD_PARTNER_PUBLIC_ID` stripped or `None`
  - `load_wait_bounds() -> tuple[float, float]` — `LOAD_WAIT_MIN` default `0.1`, `LOAD_WAIT_MAX` default `0.5`
  - `READY_PATH = "/inbound/v1/health"`
  - `PARTNERS_PATH = "/admin/v1/partners"`
  - `MIN_SMOKE_REQUESTS = 1`
  - `class PreflightError(RuntimeError)`
  - `preflight_credentials() -> str` — raises `PreflightError` if `admin_token()` empty; message must include `ADMIN_BOOTSTRAP_TOKEN`
  - `preflight_api_ready(*, host: str | None = None, timeout_seconds: float = 5.0, client: httpx.Client | None = None) -> None` — GET `READY_PATH`, require HTTP 200
  - `resolve_partner_public_id(*, host: str | None = None, token: str, client: httpx.Client | None = None, timeout_seconds: float = 5.0) -> str` — env id wins; else GET partners with `Authorization: Bearer {token}`, find slug, return `id` as str
  - `run_smoke_preflight(*, host: str | None = None, client: httpx.Client | None = None) -> str` — credentials, then ready, then resolve; return public id
  - `assert_minimum_requests(*, request_count: int, minimum: int = MIN_SMOKE_REQUESTS) -> None`
  - `loadtests.preflight:main() -> int` — print `preflight ok partner_public_id=...` or stderr + return 1

Do **not** import Locust. Do **not** create `locustfile.py`. Do **not** call `get_settings()` / pydantic env_file for the token.

- [ ] **Step 1: Write the failing tests first**

Create `tests/unit/test_load_helpers.py`:

```python
"""Unit tests for load harness helpers (no Locust / Compose required)."""

from __future__ import annotations

import httpx
import pytest

from loadtests.config import admin_token, load_host, load_wait_bounds, partner_slug
from loadtests.preflight import (
    READY_PATH,
    PreflightError,
    assert_minimum_requests,
    preflight_api_ready,
    preflight_credentials,
    resolve_partner_public_id,
    run_smoke_preflight,
)

DEMO_TOKEN = "demo-admin-bootstrap-token-not-for-prod"
ACME_ID = "0194a2b3-c4d5-7890-abcd-ef1234567890"


def test_load_host_prefers_load_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_HOST", "http://127.0.0.1:9999/")
    monkeypatch.setenv("BASE_URL", "http://example:8000")
    assert load_host() == "http://127.0.0.1:9999"


def test_load_host_falls_back_to_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_HOST", raising=False)
    monkeypatch.setenv("BASE_URL", "http://api.example:8000/")
    assert load_host() == "http://api.example:8000"


def test_load_host_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_HOST", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    assert load_host() == "http://127.0.0.1:8000"


def test_admin_token_prefers_load_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_ADMIN_TOKEN", "from-load")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "from-bootstrap")
    assert admin_token() == "from-load"


def test_admin_token_uses_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "from-bootstrap")
    assert admin_token() == "from-bootstrap"


def test_preflight_credentials_rejects_missing_process_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Must fail even when repo `.env` has ADMIN_BOOTSTRAP_TOKEN (no pydantic env_file leak)."""
    monkeypatch.delenv("ADMIN_BOOTSTRAP_TOKEN", raising=False)
    monkeypatch.delenv("LOAD_ADMIN_TOKEN", raising=False)
    with pytest.raises(PreflightError, match="ADMIN_BOOTSTRAP_TOKEN"):
        preflight_credentials()


def test_preflight_credentials_accepts_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", DEMO_TOKEN)
    assert preflight_credentials() == DEMO_TOKEN


def test_ready_path_is_inbound_health() -> None:
    assert READY_PATH == "/inbound/v1/health"


def test_preflight_api_ready_accepts_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/inbound/v1/health"
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    preflight_api_ready(host="http://test", client=client)


def test_preflight_api_ready_rejects_503() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "not_ready"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    with pytest.raises(PreflightError, match="health"):
        preflight_api_ready(host="http://test", client=client)


def test_preflight_api_ready_rejects_transport_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    with pytest.raises(PreflightError, match="health"):
        preflight_api_ready(host="http://test", client=client)


def test_resolve_partner_uses_env_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_PARTNER_PUBLIC_ID", ACME_ID)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call partners list when env id is set")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    assert resolve_partner_public_id(host="http://test", token=DEMO_TOKEN, client=client) == ACME_ID


def test_resolve_partner_finds_slug_in_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_PARTNER_PUBLIC_ID", raising=False)
    monkeypatch.setenv("LOAD_PARTNER_SLUG", "acme-erp")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/admin/v1/partners"
        assert request.headers["Authorization"] == f"Bearer {DEMO_TOKEN}"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": ACME_ID, "slug": "acme-erp", "name": "Acme ERP", "status": "active"},
                    {
                        "id": "0194ffff-ffff-7fff-8000-000000000001",
                        "slug": "flaky-logistics",
                        "name": "Flaky",
                        "status": "active",
                    },
                ],
                "total": 2,
                "limit": 50,
                "offset": 0,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    assert resolve_partner_public_id(host="http://test", token=DEMO_TOKEN, client=client) == ACME_ID


def test_resolve_partner_errors_when_slug_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_PARTNER_PUBLIC_ID", raising=False)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [], "total": 0, "limit": 50, "offset": 0},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    with pytest.raises(PreflightError, match="acme-erp"):
        resolve_partner_public_id(host="http://test", token=DEMO_TOKEN, client=client)


def test_run_smoke_preflight_requires_credentials_before_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADMIN_BOOTSTRAP_TOKEN", raising=False)
    monkeypatch.delenv("LOAD_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("LOAD_PARTNER_PUBLIC_ID", ACME_ID)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("health must not be called without credentials")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    with pytest.raises(PreflightError, match="ADMIN_BOOTSTRAP_TOKEN"):
        run_smoke_preflight(host="http://test", client=client)


def test_assert_minimum_requests_rejects_zero() -> None:
    with pytest.raises(PreflightError, match="0 HTTP request"):
        assert_minimum_requests(request_count=0)


def test_assert_minimum_requests_accepts_positive() -> None:
    assert_minimum_requests(request_count=3, minimum=1)


def test_partner_slug_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_PARTNER_SLUG", raising=False)
    assert partner_slug() == "acme-erp"


def test_load_wait_bounds_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_WAIT_MIN", raising=False)
    monkeypatch.delenv("LOAD_WAIT_MAX", raising=False)
    assert load_wait_bounds() == (0.1, 0.5)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/andrey_py_dev/Dev/_real_projects/2_b2b_partner_integration_hub
uv run pytest tests/unit/test_load_helpers.py -v
```

Expected: FAIL (`ModuleNotFoundError: loadtests` or missing names). Record command + snippet in the report as RED.

- [ ] **Step 3: Minimal implementation**

`loadtests/__init__.py` empty or a one-line package docstring.

`loadtests/config.py`:

```python
"""Environment-backed defaults for load scenarios (process env only)."""

from __future__ import annotations

import os
from typing import Final

DEFAULT_LOAD_HOST: Final = "http://127.0.0.1:8000"
DEFAULT_PARTNER_SLUG: Final = "acme-erp"


def load_host() -> str:
    raw = os.environ.get("LOAD_HOST") or os.environ.get("BASE_URL") or DEFAULT_LOAD_HOST
    return raw.rstrip("/")


def admin_token() -> str:
    """Admin Bearer token from process env only (no pydantic `.env` file)."""
    return (
        os.environ.get("LOAD_ADMIN_TOKEN") or os.environ.get("ADMIN_BOOTSTRAP_TOKEN") or ""
    ).strip()


def partner_slug() -> str:
    return (os.environ.get("LOAD_PARTNER_SLUG") or DEFAULT_PARTNER_SLUG).strip()


def partner_public_id_from_env() -> str | None:
    value = os.environ.get("LOAD_PARTNER_PUBLIC_ID", "").strip()
    return value or None


def load_wait_bounds() -> tuple[float, float]:
    min_wait = float(os.environ.get("LOAD_WAIT_MIN", "0.1"))
    max_wait = float(os.environ.get("LOAD_WAIT_MAX", "0.5"))
    return min_wait, max_wait
```

`loadtests/preflight.py` must:

- Import httpx at module top
- `preflight_api_ready`: if `client` is given, `client.get(READY_PATH)`; else `httpx.Client` GET `f"{base}{READY_PATH}"`. HTTP ≠ 200 or `httpx.HTTPError` → `PreflightError` whose message includes `health`
- `resolve_partner_public_id`: if `partner_public_id_from_env()` return it; else GET `PARTNERS_PATH` with params `limit=50`, `offset=0` and header `Authorization: Bearer {token}`. Parse `items`; match `slug == partner_slug()`; return `str(item["id"])`. Missing slug / non-200 / HTTPError → `PreflightError` mentioning the slug
- `run_smoke_preflight`: `token = preflight_credentials()`; `preflight_api_ready(...)`; return `resolve_partner_public_id(host=..., token=token, client=...)`
- `main()`: try `run_smoke_preflight()`; print `preflight ok partner_public_id={id}` stdout; on `PreflightError` print `preflight failed: ...` to stderr and return 1

Add `"loadtests"` to `[tool.ruff] src` list.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_load_helpers.py -v
uv run ruff check loadtests tests/unit/test_load_helpers.py
uv run ruff format --check loadtests tests/unit/test_load_helpers.py
```

Expected: all tests PASS, ruff 0.

- [ ] **Step 5: Do not commit.** Write the report to the path in the dispatch.

**Acceptance:**
- All tests in `tests/unit/test_load_helpers.py` PASS
- Missing process-env token fails before any HTTP (test proves it)
- No Locust import
- No `git commit`

---

### Task 2: locustfile + uv group load + smoke scripts + Make + .env.example

**Status:** complete — review APPROVE (2026-03-31). No commit. Minors: Makefile help wording, sed parse, locust --list class-only.

**Files:**
- Create: `loadtests/locustfile.py`
- Create: `scripts/load_smoke.sh` (executable)
- Create: `scripts/load_locust_ui.sh` (executable)
- Create: `tests/unit/test_load_scripts.py` (pin Makefile + script flags; no Docker)
- Modify: `pyproject.toml` — `[dependency-groups] load = ["locust[otel]>=2.32,<3"]`; keep `loadtests` in ruff src
- Modify: `uv.lock` via `uv lock` (or `uv sync --group load`)
- Modify: `Makefile` — `.PHONY` + help + targets below; **keep** `load-k6` unchanged
- Modify: `.env.example` — comment block for `LOAD_*` knobs only (do not put live secrets)

**Interfaces:**
- Consumes: Task 1 helpers
- Produces:
  - `class HubOutboundUser(locust.HttpUser)` with `wait_time` from `load_wait_bounds()`: if both ≤ 0 then `constant(0)` else `between(min, max)`
  - `@task(3) accept_outbound_event` → POST `/internal/v1/outbound/events`, name `/internal/v1/outbound/events`, expect 202
  - `@task(1) inbound_health` → GET `/inbound/v1/health`, name `/inbound/v1/health`, expect 200
  - `on_start`: read token via `admin_token()`; if empty, raise `PreflightError` (Locust will fail the user). Resolve partner id via `os.environ["LOAD_PARTNER_PUBLIC_ID"]` (smoke script exports it); if missing, call `resolve_partner_public_id` **only if** no running asyncio loop problem — prefer env. Smoke script **must** export `LOAD_PARTNER_PUBLIC_ID` from preflight stdout so Locust workers do not call `asyncio.run`.
  - `@events.quitting` listener: `assert_minimum_requests` else `environment.process_exit_code = 1`
  - Correlation: `uuid6.uuid7()` for `correlation_id` JSON field and header `X-Correlation-Id`
  - Idempotency: unique string per request (`locust-{user}-{time}` or uuid4)

`scripts/load_smoke.sh` (bash `set -euo pipefail`):

1. `cd` repo root
2. Defaults: `LOAD_HOST=http://127.0.0.1:8000`, `LOAD_USERS=2`, `LOAD_SPAWN_RATE=1`, `LOAD_RUN_TIME=10s`, HTML `.local/locust/smoke.html`, CSV prefix `.local/locust/smoke`
3. **Do not** source `.env`
4. `mkdir -p` artifact dirs
5. `uv run python -m loadtests.preflight` — capture stdout; parse `partner_public_id=...`; `export LOAD_PARTNER_PUBLIC_ID`; nonzero → exit that code
6. `uv run --group load locust -f loadtests/locustfile.py --headless --host "$LOAD_HOST" -u ... -r ... -t ... --exit-code-on-error 1 --html ... --csv ...`
7. Do **not** pass `--otel` in this script in Wave 1

`scripts/load_locust_ui.sh`:

1. If TCP `127.0.0.1:8089` is open, log FAIL and exit 1 **before** preflight (bash: `(echo >/dev/tcp/127.0.0.1/8089)`)
2. Same preflight + export partner id
3. `uv run --group load locust -f loadtests/locustfile.py --host "$LOAD_HOST"` (web UI; official default port 8089)

Makefile (add; do not remove existing targets):

```make
stack-up: compose-up

stack-down:
	$(COMPOSE) -f docker-compose.yml down --remove-orphans

load-locust:
	chmod +x scripts/load_smoke.sh
	LOAD_HOST=$(LOAD_HOST) LOAD_USERS=$(LOAD_USERS) LOAD_SPAWN_RATE=$(LOAD_SPAWN_RATE) \
		LOAD_RUN_TIME=$(LOAD_RUN_TIME) ./scripts/load_smoke.sh

load-locust-ui:
	chmod +x scripts/load_locust_ui.sh
	LOAD_HOST=$(LOAD_HOST) ./scripts/load_locust_ui.sh
```

Defaults at top of Makefile (or next to targets): `LOAD_HOST ?= http://127.0.0.1:8000`, `LOAD_USERS ?= 2`, `LOAD_SPAWN_RATE ?= 1`, `LOAD_RUN_TIME ?= 10s`. Update `.PHONY` and `help`.

Pin tests in `tests/unit/test_load_scripts.py` (read files as text):

- `Makefile` contains `load-locust`, `stack-up`, `stack-down`, `--remove-orphans`, and does **not** contain `down -v` on stack-down
- `scripts/load_smoke.sh` contains `--headless`, `--exit-code-on-error 1`, `--html`, `--csv`, `python -m loadtests.preflight`, and does **not** contain `source .env`
- `scripts/load_locust_ui.sh` contains `8089` and does not source `.env`
- `pyproject.toml` contains `locust[otel]`
- `loadtests/locustfile.py` contains `accept_outbound_event`, `inbound_health`, `/internal/v1/outbound/events`, `process_exit_code`

- [ ] **Step 1: Write `tests/unit/test_load_scripts.py` first** (fail: files missing)
- [ ] **Step 2: RED** — `uv run pytest tests/unit/test_load_scripts.py tests/unit/test_load_helpers.py -v`
- [ ] **Step 3: Implement locustfile, scripts, Makefile, pyproject, `.env.example` comments:**

```bash
# Load-test knobs (export in the shell; scripts do not source this file)
# LOAD_HOST=http://127.0.0.1:8000
# LOAD_ADMIN_TOKEN=   # optional override of ADMIN_BOOTSTRAP_TOKEN
# LOAD_PARTNER_SLUG=acme-erp
# LOAD_PARTNER_PUBLIC_ID=  # optional; otherwise resolved via Admin API after seed
# LOAD_USERS=2
# LOAD_SPAWN_RATE=1
# LOAD_RUN_TIME=10s
# LOAD_WAIT_MIN=0.1
# LOAD_WAIT_MAX=0.5
```

Locust POST body:

```python
body = {
    "partner_id": self.partner_id,
    "event_type": "order.created",
    "payload": {"order_id": f"locust-{idem}", "amount": 1.0},
    "idempotency_key": idem,
    "correlation_id": corr,
}
```

Headers: `Authorization: Bearer {token}`, `Content-Type: application/json`, `X-Correlation-Id: {corr}`. Use `catch_response=True`; fail if status != 202 or JSON `status` != `accepted`.

- [ ] **Step 4: GREEN** — pytest both test files; `uv sync --group load`; `uv run --group load locust -f loadtests/locustfile.py --list` shows both tasks; ruff on new files
- [ ] **Step 5: Do not commit**

**Acceptance:** `make load-k6` recipe unchanged; locust `--list` shows both tasks; pin tests PASS; no `--otel` on default smoke.

---

### Task 3: live full stack-up + smoke + runbook + fail-closed evidence + stack-down

**Status:** complete — review APPROVE (2026-03-31). No commit.

**Files:**
- Create: `docs/runbooks/load-testing.md`
- Create: `docs/perf/locust-smoke.md`
- Modify: `docs/perf/README.md` — add Locust row; keep k6 row
- Modify: `AGENTS.md` §8 commands + §0.2 runbooks + §10.5 pointer
- Modify: `README.md` — mention `make load-locust` / `make stack-up` without claiming NFR
- Modify: `CONTRIBUTING.md` — scripts do not source `.env`; load extra `uv sync --group load`

**Do not** change `app/` or k6 scripts.

- [ ] **Step 1: If another numbered `_real_projects` compose stack holds frozen ports, `docker compose -p <name> down` that project only (no `-v`).** Hub ports were free at Wave 0; re-check.
- [ ] **Step 2: Bring the full stack up and seed**

```bash
cd /home/andrey_py_dev/Dev/_real_projects/2_b2b_partner_integration_hub
cp -n .env.example .env || true
make stack-up
make seed
```

Wait until `hub-api` healthy. Confirm `hub-outbound-worker` and `hub-outbox-relay` are running (`docker compose -p b2b-partner-integration-hub ps`). Do not load-test against host-uvicorn.

- [ ] **Step 3: Fail-closed (no credentials) — before a successful smoke**

```bash
env -u ADMIN_BOOTSTRAP_TOKEN -u LOAD_ADMIN_TOKEN ./scripts/load_smoke.sh
```

Expected: **nonzero** exit, stderr `preflight failed`, **no** Locust host stats with HTTP requests. Paste command + stderr into `docs/perf/locust-smoke.md`.

- [ ] **Step 4: Successful smoke**

```bash
set -a && source .env && set +a
make load-locust
```

Expected: exit 0; CSV under `.local/locust/` with both named endpoints; HTML written. Record in `docs/perf/locust-smoke.md`: command, overlay (**default compose**, not perf), users/spawn/duration, `# reqs`, RPS, fail%, p50/p99 if present, note that this is accept-path smoke not §8.1 stand NFR. Do **not** invent RPS.

- [ ] **Step 5: Optional UI port check (if 8089 free):** run `LOAD_HOST=http://127.0.0.1:8000 timeout 3 ./scripts/load_locust_ui.sh` or document skip. If 8089 busy, the script must exit nonzero — record that.

- [ ] **Step 6: Docs** — runbook: prerequisites `stack-up` + `seed`, env export, `make load-locust` / `load-locust-ui` / existing `make load-k6`, fail-closed, gitignore `.local/`, Locust measures 202 accept not delivery, k6 remains Stage 3 persist-path regression. English only.

- [ ] **Step 7: Tear down**

```bash
make stack-down
```

Confirm containers for project `b2b-partner-integration-hub` are not running. **No** `-v`.

- [ ] **Step 8: Do not commit**

**Acceptance:** tracked runbook + smoke facts from **this** live run; fail-closed evidence; stack down; AGENTS/README updated; k6 docs still accurate.

---

## Out of Wave 1 (later plans)

- Locust `--otel` + Grafana dashboard from live names; k6 `experimental-prometheus-rw` on compose network; dashboard 19665
- CI `load-harness` / `load-locust-smoke`; pre-commit expand
- `docker-compose.perf.yml` / `make perf-up`; ceiling hunt; bottleneck ADR+code; remesure
