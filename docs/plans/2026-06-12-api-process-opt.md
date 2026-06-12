# Wave 5 — API/process limiter (software + overlay honesty)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the **named Wave 4 limiter** (API/process) with software on the accept path plus an honest perf overlay CPU quota — then leave remesure to Wave 6. Do **not** treat `--scale`, `max_connections++`, or default-stack CPU bumps as the fix.

**Architecture:** Clock A stopped because four uvicorn workers share Compose `cpus: "1.0"` and the accept path rebuilds a `Draft202012Validator` on every outbound enqueue. Fix (1) reuse compiled JSON Schema validators keyed by schema identity; (2) reuse one SQLAlchemy engine/sessionmaker per database URL in workers/relay (API lifespan already does this); (3) override **only** `hub-api` CPU quota in `docker-compose.perf.yml` to `4.0` so `--workers 4` is not starved. Default `docker-compose.yml` stays `cpus: "1.0"`. Do not change Kafka prefetch or Postgres `max_connections`.

**Tech stack:** jsonschema Draft 2020-12, SQLAlchemy 2 asyncio engine, Compose overlay, existing pytest pin tests.

## Global constraints

- SoT: `spec.md` v3.1 EN + ADR 001–010 + `AGENTS.md`.
- **Do not commit. Not Stage Done.** No port shifts. No `down -v`. No prune.
- Named limiter is **API/process**, not relay/workers/DB. Do not “fix” outbox `send_and_wait`, prefetch, or `--scale`.
- Do **not** rewrite spec §8.1 table (100/500/2000).
- Do **not** raise Postgres `max_connections`. Pool remains SQLAlchemy defaults unless a later hunt names pool budget.
- Do **not** empty `REDIS_URL`. Do not touch inbound argon2.
- Overlay CPU override is **not** NFR proof; it makes `--workers 4` usable. Default stack-up stays 1 CPU.
- English docs. No `Task N` in `app/` / Compose / scripts.
- Implementer ≠ Reviewer. TDD: failing pin test → implement → PASS.
- Official: [jsonschema Validator](https://python-jsonschema.readthedocs.io/en/stable/validate/), [SQLAlchemy pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html) (engine is process-wide).

## Git vs gitignore

Tracked: `app/domain/services/schema_registry.py`, `app/db/session.py`, `docker-compose.perf.yml`, unit pin tests, runbook/AGENTS notes. Ignored: `.env`, `.venv/`, `.local/`, `.superpowers/`.

---

### Task 1: reuse compiled JSON Schema validators + process-wide sessionmaker

**Files:**
- Modify: `app/domain/services/schema_registry.py`
- Modify: `app/db/session.py`
- Modify: `tests/unit/test_schema_registry.py` (add cache tests; keep existing validate tests green)
- Create: `tests/unit/test_sessionmaker_reuse.py`
- Modify: `docs/perf/ceiling-prodlike.md` (one short “Wave 5 intended fix” note — no new RPS claims)

**Forbidden:** Compose live hunt, overlay CPU change (Task 2), `app/` HMAC/argon2, pool_size knobs, git commit.

**Validator cache design:**

- `Draft202012Validator(schema_row.json_schema)` must not run on every `validate_payload` call for the same schema identity.
- Key: `(schema_row.id, schema_row.version)` (UUIDv7 id + integer version). Schemas are versioned; in-place same-version edits are out of scope.
- Module-level dict `_VALIDATORS: dict[tuple[UUID, int], Draft202012Validator]`.
- Helper `_validator_for(schema_row: PayloadSchema) -> Draft202012Validator` used by `validate_payload`.
- No-row / non-ACTIVE / event_type mismatch still no-op (existing tests).
- Invalid payload still raises `SchemaValidationError`.

**Sessionmaker design:**

- `get_sessionmaker(settings)` currently calls `create_async_engine` on every invocation. API lifespan in `app/main.py` already builds one engine — do **not** double-engine the API. Workers/relay call `get_sessionmaker` once today; still cache by `settings.database_url` so a second call cannot leak another pool.
- Module dict `_SESSIONMAKERS: dict[str, async_sessionmaker[AsyncSession]]`.
- `reset_sessionmakers()` for tests: dispose each `maker.kw["bind"]` or `maker().bind` — use `async_sessionmaker.kw["bind"]` if present; otherwise iterate and `await engine.dispose()` via a small **sync** dispose: `engine.sync_engine.dispose()` is wrong for async. Prefer storing `(engine, sessionmaker)` and `asyncio.run(engine.dispose())` only if no running loop; for unit tests, keep a list of engines and `await engine.dispose()` in an async test, or use `engine.sync_dispose()` — **SQLAlchemy 2 async engine:** `await engine.dispose()`. Unit test should be async pytest.
- Public signature `get_sessionmaker(settings: Settings) -> async_sessionmaker[AsyncSession]` unchanged.

- [ ] **Step 1: Failing tests** — add to `tests/unit/test_schema_registry.py`:

```python
from unittest.mock import patch
from jsonschema.validators import Draft202012Validator

from app.domain.services.schema_registry import validate_payload, _VALIDATORS


def test_validate_payload_reuses_compiled_validator_for_same_schema_id_version() -> None:
    _VALIDATORS.clear()
    row = _active_order_created_schema()
    with patch(
        "app.domain.services.schema_registry.Draft202012Validator",
        wraps=Draft202012Validator,
    ) as ctor:
        validate_payload(EVENT_TYPE, {"order_id": "a"}, row)
        validate_payload(EVENT_TYPE, {"order_id": "b"}, row)
        assert ctor.call_count == 1
```

If `_VALIDATORS` is not yet exported, the first run fails on import — that is RED. After implementation, keep `_VALIDATORS` module-private but testable (`schema_registry._VALIDATORS` without importing in the test’s production callers). Prefer testing via `schema_registry._VALIDATORS` after import of the module:

```python
from app.domain import services as schema_registry_mod
```

Simplest: import `app.domain.services.schema_registry as schema_registry` and use `schema_registry._VALIDATORS`.

Add `tests/unit/test_sessionmaker_reuse.py`:

```python
import pytest

from app.config import Settings
from app.db.session import get_sessionmaker, reset_sessionmakers


@pytest.mark.asyncio
async def test_get_sessionmaker_reuses_engine_for_same_url() -> None:
    reset_sessionmakers()
    settings = Settings()  # or construct with database_url from env / test default
    a = get_sessionmaker(settings)
    b = get_sessionmaker(settings)
    assert a is b
    engine = a.kw["bind"]
    await engine.dispose()
    reset_sessionmakers()
```

If `Settings()` requires env, use the same factory other unit tests use (`get_settings` + `cache_clear`, or a minimal `Settings` with `database_url="postgresql+asyncpg://hub:hub@localhost:5432/hub"`). Match existing test style in `tests/unit/`. If `async_sessionmaker.kw["bind"]` is missing on this SQLAlchemy version, compare `a.bind` / `a.kw.get("bind")` — read the installed SQLAlchemy 2 API in-repo before asserting.

- [ ] **Step 2: RED** `uv run pytest tests/unit/test_schema_registry.py tests/unit/test_sessionmaker_reuse.py -q`

- [ ] **Step 3: Implement** `schema_registry.py`:

```python
_VALIDATORS: dict[tuple[UUID, int], Draft202012Validator] = {}


def _validator_for(schema_row: PayloadSchema) -> Draft202012Validator:
    key = (schema_row.id, schema_row.version)
    validator = _VALIDATORS.get(key)
    if validator is None:
        validator = Draft202012Validator(schema_row.json_schema)
        _VALIDATORS[key] = validator
    return validator
```

Use `_validator_for(schema_row).validate(payload)` inside `validate_payload`. Keep no-op branches unchanged.

`session.py`: cache sessionmaker by URL; add `reset_sessionmakers()` that disposes engines. Do not change `app/main.py` lifespan (already one engine).

- [ ] **Step 4: GREEN** same pytest files + existing `tests/unit/test_schema_registry.py` cases still pass.

- [ ] **Step 5: Docs** — in `docs/perf/ceiling-prodlike.md` add a short “Intended Wave 5 change (not remesured)” bullet: validator reuse + sessionmaker cache. No laptop RPS as NFR.

- [ ] **Step 6:** Do not commit. Do not start Compose.

**Acceptance:** ctor call_count == 1; sessionmaker identity equal; existing schema tests PASS; no overlay CPU change yet.

---

### Task 2: perf overlay CPU quota for four workers

**Files:**
- Modify: `docker-compose.perf.yml` — `hub-api.deploy.resources.limits.cpus: "4.0"` (keep base memory unless Compose merge requires repeating `memory`)
- Modify: `tests/unit/test_perf_overlay.py` — assert overlay text contains `cpus` and `4.0`; assert `docker-compose.yml` hub-api still has `cpus: "1.0"`
- Modify: `docs/runbooks/load-testing.md` knob table — overlay API CPU 4.0 vs default 1.0; not §8.1 proof
- Modify: `AGENTS.md` §8 / §10.5 one line: overlay CPU is characterization, not NFR

**Forbidden:** live Locust, `app/` changes, default compose CPU change, `max_connections`, git commit.

- [ ] **Step 1: Failing pin** in `tests/unit/test_perf_overlay.py`:

```python
def test_perf_overlay_raises_api_cpu_quota_not_default_stack() -> None:
    overlay = (ROOT / "docker-compose.perf.yml").read_text(encoding="utf-8")
    base = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "cpus:" in overlay
    assert '"4.0"' in overlay or "4.0" in overlay
    assert 'cpus: "1.0"' in base
```

- [ ] **Step 2: RED** pytest that test.

- [ ] **Step 3:** Add under `hub-api` in `docker-compose.perf.yml`:

```yaml
    deploy:
      resources:
        limits:
          cpus: "4.0"
```

Do not change `docker-compose.yml`. Do not add more `--scale`.

- [ ] **Step 4: GREEN** pin tests. Update runbook knob table. No Compose up.

**Acceptance:** default stack still 1.0 CPU; overlay 4.0; pin tests PASS; no live hunt.

---

## Out of Wave 5

Wave 6: `make perf-up` (rebuild images), same Locust wait=0 hold, Clock A + Clock B, reclassify limiter, footnote facts, `make stack-down`.
