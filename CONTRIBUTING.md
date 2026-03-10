# Contributing

## Local workflow

1. Read [`AGENTS.md`](AGENTS.md) and [`spec.md`](spec.md) (English product SoT) for the slice you touch.
2. Prefer TDD for domain, API, and workers.
3. `make ci` must stay green (ruff, mypy, unit tests).
4. Do not commit secrets. All secrets live in `.env` (gitignored). `.env.example` is the only tracked env template.
5. Commits happen when the maintainer asks — do not push or open PRs unless asked.

## Pre-commit hooks

Install once after `uv sync --group dev`:

```bash
uv run pre-commit install
```

Hooks run Ruff (pinned to lockfile **0.5.7**), standard file checks, and a local `import loadtests.locustfile` check. **No Docker** — hooks never start Compose or a live API.

**Untracked files:** On a fresh clone or before the first commit, `pre-commit run --all-files` only checks paths in `git ls-files`. Hooks **skip** files that are not staged/tracked — `git add` the paths you want checked first. To run hooks on specific paths without relying on the index:

```bash
uv run pre-commit run --files loadtests/locustfile.py tests/unit/test_ci_load_jobs.py
```

This repository may have no commits yet; use `--files` for local proof when needed.

## Load harness

Host-side Locust requires the optional dependency group:

```bash
uv sync --group load
```

Load scripts (`scripts/load_smoke.sh`, `scripts/load_locust_ui.sh`) **do not** `source .env`. Export credentials in your shell before `make load-locust` or `make load-locust-ui`:

```bash
set -a && source .env && set +a
```

Full-stack prerequisites and fail-closed checks: [`docs/runbooks/load-testing.md`](docs/runbooks/load-testing.md).

## Contracts

- HTTP: live `http://localhost:8000/docs` is the source of truth. Do not add markdown endpoint catalogs.
- Kafka: [`docs/asyncapi/asyncapi.yaml`](docs/asyncapi/asyncapi.yaml).
- Dual-id: never expose sequential partner/delivery `id` in JSON, OpenAPI, UI, or Kafka.

## Images and Compose

Non-root runtime user, no `COPY .env`, healthchecks, `stop_grace_period: 30s`. Frozen host ports are listed in `AGENTS.md` §1.1. Compose project and default network name: `b2b-partner-integration-hub`.
