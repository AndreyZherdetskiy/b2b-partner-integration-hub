# CI load jobs + pre-commit (Wave 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add sibling GitHub Actions jobs for the Locust harness (no stack) and a full-stack Locust smoke, plus tracked pre-commit hooks. Do not fold load into `make ci`. Do not run k6/Grafana/OTEL in CI.

**Architecture:** Keep existing GHA jobs (`lint`, `typecheck`, `test-unit`, `test-contract`, `asyncapi`). Add `load-harness` (`make load-harness`: uv group `load`, helper pytest, `locust --list`) and `load-locust-smoke` (`cp .env.example .env`, `make stack-up`, `make load-locust`, `if: always()` `make stack-down`). Pre-commit: Ruff **v0.5.7** (matches `uv.lock`; hook id `ruff`), standard file hooks, local `import loadtests.locustfile`. No Docker in hooks.

**Tech Stack:** GitHub Actions `ubuntu-latest`, uv 3.12, Docker (smoke job only), pre-commit, Ruff 0.5.7.

## Global Constraints

- Product SoT: `spec.md` v3.1 EN + ADR 001–010 + `AGENTS.md`.
- **Do not commit.** Not Stage Done.
- Locust remains Wave 1 accept-path smoke. Default CI smoke **without** `LOAD_LOCUST_OTEL=1`. No k6 matrix.
- Demo secrets only from `.env.example` (`ADMIN_BOOTSTRAP_TOKEN`). No GitHub secrets for this wave.
- Do not merge load jobs into `make ci`.
- Pre-commit: **no Docker**, no live API. Ruff rev = uv.lock **0.5.7**. Hook id is `ruff` (not `ruff-check`) at this tag: https://docs.astral.sh/ruff/integrations/#pre-commit
- Fresh/untracked repo: `pre-commit run --all-files` only sees `git ls-files`. Document in CONTRIBUTING that untracked files skip hooks; use `pre-commit run --files <paths>` for local proof.
- Frozen ports / compose project `b2b-partner-integration-hub` unchanged.
- Docs English. No `Task N` in `app/`, Compose, workflows, or scripts.
- Implementer ≠ Reviewer.

## Git vs gitignore

Tracked: `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, Makefile, tests, CONTRIBUTING.md, AGENTS.md, this plan. Ignored: `.env`, `.venv/`, `.local/`, `.superpowers/`.

---

### Task 1: GHA load jobs + Makefile `load-harness` + pin tests (no live stack)

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `Makefile` (`load-harness`)
- Create: `tests/unit/test_ci_load_jobs.py`

- [ ] **Step 1: Failing pin tests** `tests/unit/test_ci_load_jobs.py` (parse YAML as **text**; do not add PyYAML):

```python
"""Pin GitHub Actions load jobs (no live stack)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def test_existing_jobs_remain() -> None:
    for job in ("lint:", "typecheck:", "test-unit:", "test-contract:", "asyncapi:"):
        assert job in WORKFLOW


def test_python_ci_job_does_not_run_locust_stack() -> None:
    # Existing quality jobs must not call stack-up / load-locust
    lint_block = WORKFLOW.split("lint:")[1].split("typecheck:")[0]
    assert "make stack-up" not in lint_block
    assert "make load-locust" not in lint_block


def test_load_harness_job_exists() -> None:
    assert "load-harness:" in WORKFLOW
    harness = WORKFLOW.split("load-harness:")[1].split("load-locust-smoke:")[0]
    assert "make load-harness" in harness
    assert "make stack-up" not in harness


def test_load_locust_smoke_job() -> None:
    assert "load-locust-smoke:" in WORKFLOW
    smoke = WORKFLOW.split("load-locust-smoke:")[1]
    assert "timeout-minutes:" in smoke
    assert "cp .env.example .env" in smoke
    assert "make stack-up" in smoke
    assert "make load-locust" in smoke
    assert "if: always()" in smoke
    assert "make stack-down" in smoke
    assert "LOAD_LOCUST_OTEL=1" not in smoke
    assert "make load-k6" not in smoke


def test_makefile_load_harness() -> None:
    assert "load-harness:" in MAKEFILE
    block = MAKEFILE.split("load-harness:")[1].split("\n\n")[0]
    assert "--list" in block
    assert "stack-up" not in block
    assert "test_load_helpers.py" in block
```

- [ ] **Step 2: RED** `uv run pytest tests/unit/test_ci_load_jobs.py -v`

- [ ] **Step 3: Makefile**

```make
load-harness:
	uv sync --python 3.12 --frozen --group load --group dev
	uv run pytest tests/unit/test_load_helpers.py tests/unit/test_load_scripts.py tests/unit/test_load_grafana_helpers.py tests/unit/test_ci_load_jobs.py -q
	uv run --group load locust -f loadtests/locustfile.py --list
```

Add to `.PHONY` and help. Do **not** add `load-harness` to `make ci`.

- [ ] **Step 4: Workflow** — sibling jobs after existing ones:

`load-harness`: checkout, astral-sh/setup-uv@v4 (match existing workflow), python 3.12, `make load-harness`.

`load-locust-smoke`: `timeout-minutes: 60`, checkout, setup-uv, `cp .env.example .env`, `uv sync --frozen --group load --group dev` (or rely on make stack-up + load-locust), `make stack-up`, then `set -a && source .env && set +a && make load-locust`, then `if: always()` `make stack-down`.

Keep `on: push` / `pull_request` as today. Do not add k6 job.

- [ ] **Step 5: GREEN** pytest pin tests + `make load-harness` **without Docker**. Do not stack-up. Do not commit.

**Acceptance:** existing five jobs still present; load-harness no stack; smoke job always downs; pin tests PASS.

---

### Task 2: pre-commit + CONTRIBUTING + pin tests

**Files:**
- Modify: `.pre-commit-config.yaml`
- Create: `tests/unit/test_precommit_config.py`
- Modify: `CONTRIBUTING.md`, `AGENTS.md` §1/§8/§10.5, `docs/runbooks/load-testing.md` CI section

- [ ] **Step 1: Failing tests** asserting:
  - ruff-pre-commit `rev: v0.5.7` and hook id `ruff` (not `ruff-check`)
  - `ruff-format` present
  - pre-commit-hooks: `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files`, `detect-private-key`
  - local hook `import loadtests.locustfile` with `uv run --group load python -c "import loadtests.locustfile"`
  - no `docker` in `.pre-commit-config.yaml`

- [ ] **Step 2: RED** then implement config (keep existing ruff hooks; add pre-commit-hooks repo pin e.g. `v4.6.0` or `v5.0.0` — pin one rev and name it in the report).

- [ ] **Step 3: CONTRIBUTING** — `uv run pre-commit install`; note that on a repo with **no commits / untracked files**, `pre-commit run --all-files` only sees `git ls-files`; use `pre-commit run --files ...` for proof. No Docker in hooks.

- [ ] **Step 4: GREEN** pytest; `uv run pre-commit run --files loadtests/locustfile.py tests/unit/test_ci_load_jobs.py tests/unit/test_precommit_config.py .github/workflows/ci.yml .pre-commit-config.yaml` (may auto-fix whitespace — include those fixes). Do **not** require a full `--all-files` green if most of the tree is still untracked.

- [ ] **Step 5:** AGENTS.md CI bullet + `make load-harness`; runbook CI paragraph. Do not commit.

**Acceptance:** hooks tracked; locustfile import hook; CONTRIBUTING skip note; pin tests PASS.

---

## Out of Wave 3

`docker-compose.perf.yml`, ceiling hunt, bottleneck, k6 breakpoint, Grafana in CI.
