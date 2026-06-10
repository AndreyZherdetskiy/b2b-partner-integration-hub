# Locust OTEL + k6 Grafana remote-write (Wave 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Opt-in Locust `--otel` into the **existing** Hub Collector → Prometheus → Grafana path, and add k6 Prometheus remote-write on the Compose network with official dashboard **19665**. Locust Wave 1 smoke remains accept-path smoke; k6 Stage 3 persist script remains. Neither path is spec §8.1 stand proof.

**Architecture:** Grafana, Prometheus, and OTel Collector **already** run in default `docker-compose.yml` (host **4318** frozen). Do **not** add Alloy or a second Grafana. Do **not** create `docker-compose.otlp.yml` (would collide with frozen Collector :4318). Default `make load-locust` stays without `--otel`. `LOAD_LOCUST_OTEL=1` fail-closes unless Compose network `b2b-partner-integration-hub` exists and host TCP **4318** accepts. k6 Grafana path: `docker run --network b2b-partner-integration-hub` with `BASE=http://hub-api:8000` and `-o experimental-prometheus-rw`. Prometheus gains `--web.enable-remote-write-receiver`; host **9090** stays published (frozen). Locust Grafana panels are written **after** a live OTEL run from discovered metric names — not invented PromQL.

**Tech Stack:** Locust 2.46.x `locust[otel]` (already in uv group `load`), existing Collector (`infra/otel/collector.yaml` OTLP HTTP 4318 → Prometheus exporter 8889), Prometheus v2.54.1, Grafana 11.2.0 (folder **Partner Hub**, datasource uid **`Prometheus`**), `grafana/k6`, dashboard **19665**.

## Global Constraints

- Product SoT: `spec.md` v3.1 EN + ADR 001–010 + `AGENTS.md`. Notify Alloy overlay is **not** this repo.
- **Do not commit.** Not Stage Done. Do not rewrite spec §8.1 from laptop RPS.
- Locust additive; do not retune locustfile weights as k6 proof. Keep `make load-k6` host-network recipe. Grafana k6 path must **not** pass `--no-thresholds`.
- No locust-plugins, no sidecar exporters — only official `locust --otel` (https://docs.locust.io/en/stable/telemetry.html).
- Frozen ports: do not shift. Prometheus **9090** stays published. Collector **4318** already published — reuse it.
- Compose project / network: `b2b-partner-integration-hub`. App DNS: `hub-api`, `prometheus`, `grafana`, `otel-collector`.
- Auth: `ADMIN_BOOTSTRAP_TOKEN` / `LOAD_ADMIN_TOKEN`. Pass `ADMIN_TOKEN` into k6 (existing `load/k6/outbound_ingest.js`). Do not invent JWT mint.
- OTLP: `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, endpoint `http://127.0.0.1:4318`.
- Fail-closed Grafana Locust path: `docker network inspect b2b-partner-integration-hub` + TCP :4318. Nonzero exit; no silent skip of `--otel`.
- WSL: k6 Grafana script via stdin `k6 run -`, not a bind-mount of the JS file.
- No native histograms in this wave (`K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM` unset/false).
- Grafana dashboards live under `docs/grafana/dashboards/` (image bake). Datasource uid is **`Prometheus`** (see `infra/grafana/provisioning/datasources/datasource.yml`). Folder **Partner Hub**.
- Existing unit test `tests/unit/test_seed_and_grafana.py::test_grafana_dashboard_promql_uses_partner_slug` globs **top-level** `*.json` and requires `partner_slug` in every PromQL. Load-generator dashboards **must** either live in a subdirectory (so the glob misses them) **or** the test must skip named files `k6-prometheus.json` / `locust-otel.json`. Do not stuff fake `partner_slug` into k6/Locust PromQL.
- Default `make load-locust` / `make stack-up` without Locust OTEL. Opt-in: `make load-locust-otel`, `make load-k6-grafana`.
- Tear-down: `make stack-down` downs `docker-compose.yml` (no `-v`). No OTLP overlay file in this wave. If you add `docker-compose.perf.yml` later (Wave 4), that wave updates stack-down.
- Tracked docs English. No `Task N` in `app/`, Compose, or scripts.
- Official: Locust telemetry; k6 RW https://grafana.com/docs/k6/latest/results-output/real-time/prometheus-remote-write/ ; Prometheus `--web.enable-remote-write-receiver`; Grafana dashboard **19665**.
- k6 `http_req_failed` `rate<0.05` is **5%**. Wave 2 k6 Grafana is a **short smoke**, not breakpoint (Wave 6).
- Implementer ≠ Reviewer. No self-APPROVE.

## Git vs gitignore

| Tracked | Ignored |
|---------|---------|
| `docker-compose.yml` (prometheus command), `scripts/load_smoke.sh` (OTEL branch), `scripts/load_k6_grafana.sh`, Makefile, `docs/grafana/dashboards/k6-prometheus.json`, `docs/grafana/dashboards/locust-otel.json` (Task 2), pin tests, docs | `.env`, `.venv/`, `.local/`, `.superpowers/` |

---

## File map

| Path | Role |
|------|------|
| `docker-compose.yml` | Prometheus `command` += `--web.enable-remote-write-receiver` |
| `scripts/load_smoke.sh` | Optional `LOAD_LOCUST_OTEL=1` → fail-closed + `--otel` |
| `scripts/load_k6_grafana.sh` | k6 RW on compose network, stdin `load/k6/outbound_ingest.js` |
| `Makefile` | `load-locust-otel`, `load-k6-grafana` |
| `tests/unit/test_load_grafana_helpers.py` | Pin scripts/compose/Make |
| `docs/grafana/dashboards/k6-prometheus.json` | Official 19665, datasource uid `Prometheus` |
| `docs/grafana/dashboards/locust-otel.json` | Task 2 only, from live names |
| `tests/unit/test_seed_and_grafana.py` | Skip or subdirectory for load dashboards |

---

### Task 1: Prometheus RW + opt-in Locust OTEL + k6 Grafana runner + 19665 + pin tests (no live stack)

**Files:** all of the file map except `locust-otel.json`.

**Interfaces:**
- Consumes: Wave 1 `scripts/load_smoke.sh`, `make load-locust`, `load/k6/outbound_ingest.js`, `loadtests.preflight`
- Produces: `make load-locust-otel`, `make load-k6-grafana`; Prometheus RW receiver; k6 dashboard JSON

- [ ] **Step 1: Write failing pin tests** in `tests/unit/test_load_grafana_helpers.py`:

```python
"""Pin Locust OTEL + k6 Grafana script contracts (no live Grafana)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_prometheus_compose_enables_remote_write_receiver() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "--web.enable-remote-write-receiver" in text
    assert "9090:9090" in text


def test_load_smoke_otel_is_opt_in() -> None:
    text = (ROOT / "scripts" / "load_smoke.sh").read_text(encoding="utf-8")
    assert "LOAD_LOCUST_OTEL" in text
    assert "--otel" in text
    assert "OTEL_EXPORTER_OTLP_PROTOCOL" in text
    assert "http/protobuf" in text
    assert "http://127.0.0.1:4318" in text
    assert "docker network inspect" in text
    assert "b2b-partner-integration-hub" in text
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    default_block = makefile.split("load-locust:")[1].split("load-locust-ui:")[0]
    assert "LOAD_LOCUST_OTEL=1" not in default_block
    assert "load-locust-otel:" in makefile
    otel_block = makefile.split("load-locust-otel:")[1].split("load-k6-grafana:")[0]
    assert "LOAD_LOCUST_OTEL=1" in otel_block


def test_load_k6_grafana_uses_compose_dns_and_stdin() -> None:
    text = (ROOT / "scripts" / "load_k6_grafana.sh").read_text(encoding="utf-8")
    assert "b2b-partner-integration-hub" in text
    assert "docker network inspect" in text
    assert "experimental-prometheus-rw" in text
    assert "http://prometheus:9090/api/v1/write" in text
    assert "http://hub-api:8000" in text
    assert "run -" in text
    assert "--no-thresholds" not in text
    assert "outbound_ingest.js" in text
    assert "ADMIN_TOKEN" in text
    assert "K6_PARTNER_PUBLIC_ID" in text


def test_k6_dashboard_is_19665_family() -> None:
    path = ROOT / "docs" / "grafana" / "dashboards" / "k6-prometheus.json"
    text = path.read_text(encoding="utf-8")
    assert "k6_" in text
    assert "Prometheus" in text
    assert "locust" not in text.lower()


def test_default_load_locust_makefile_does_not_force_otel() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "load-k6-grafana:" in makefile
```

Also update `test_grafana_dashboard_promql_uses_partner_slug` so `k6-prometheus.json` and `locust-otel.json` are skipped (or move those JSON files to `docs/grafana/dashboards/load/` and keep Grafana COPY of the parent tree). If using a subdirectory, add `foldersFromFilesStructure: true` only if required for Grafana to pick them up — prefer **skip by filename** in the existing test so provisioning path stays `/etc/grafana/dashboards` as today.

- [ ] **Step 2: RED** `uv run pytest tests/unit/test_load_grafana_helpers.py -v`

- [ ] **Step 3: Prometheus command** — add `--web.enable-remote-write-receiver` next to existing `--web.enable-lifecycle`. Keep `9090:9090`. Ground: Prometheus 2.54 CLI.

- [ ] **Step 4: `scripts/load_smoke.sh` OTEL branch** when `LOAD_LOCUST_OTEL=1`:
  1. `docker network inspect b2b-partner-integration-hub` or FAIL
  2. TCP `127.0.0.1:4318` or FAIL
  3. export `OTEL_SDK_DISABLED=false`, `OTEL_SERVICE_NAME=locust`, `OTEL_TRACES_EXPORTER=otlp`, `OTEL_METRICS_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318`
  4. append `--otel` to locust args
  Default (`LOAD_LOCUST_OTEL` unset or `0`) must not add `--otel`.

- [ ] **Step 5: Makefile**

```make
load-locust-otel:
	chmod +x scripts/load_smoke.sh
	LOAD_LOCUST_OTEL=1 LOAD_HOST=$(LOAD_HOST) LOAD_USERS=$(LOAD_USERS) LOAD_SPAWN_RATE=$(LOAD_SPAWN_RATE) \
		LOAD_RUN_TIME=$(LOAD_RUN_TIME) ./scripts/load_smoke.sh

load-k6-grafana:
	chmod +x scripts/load_k6_grafana.sh
	./scripts/load_k6_grafana.sh
```

Do not set `LOAD_LOCUST_OTEL=1` on `load-locust`. Update `.PHONY` and help.

- [ ] **Step 6: `scripts/load_k6_grafana.sh`**
  - `set -euo pipefail`; do **not** source `.env`
  - Fail if network missing
  - Run `uv run python -m loadtests.preflight`; export `K6_PARTNER_PUBLIC_ID` from `partner_public_id=`
  - Token: `LOAD_ADMIN_TOKEN` else `ADMIN_BOOTSTRAP_TOKEN` from process env; empty → FAIL
  - `docker run --rm -i --network b2b-partner-integration-hub` with env `BASE=http://hub-api:8000`, `ADMIN_TOKEN`, `K6_PARTNER_PUBLIC_ID`, `K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write`, optional short `K6_VUS`/`K6_DURATION` (defaults 2 / 15s)
  - Image: `grafana/k6:0.54.0` (or current grafana/k6 tag documented in the script comment)
  - `k6 run -o experimental-prometheus-rw - < load/k6/outbound_ingest.js`
  - No `--no-thresholds`; no native histogram env

- [ ] **Step 7: Dashboard 19665** — download official JSON (Grafana.com dashboard 19665). Remap datasource uid to **`Prometheus`**. Save `docs/grafana/dashboards/k6-prometheus.json`. No Locust PromQL in this file. No `delivery_id` labels.

- [ ] **Step 8: GREEN** pytest pin tests + existing `tests/unit/test_seed_and_grafana.py` + `tests/unit/test_compose_ports.py` still pass. ruff on new test file. **Do not commit.**

**Acceptance:** default `load-locust` recipe unchanged regarding OTEL; `load-k6` unchanged; pin tests PASS; no live stack.

---

### Task 2: live OTEL + k6 RW + Locust dashboard from live names + docs + stack-down

**Files:**
- Create: `docs/grafana/dashboards/locust-otel.json`
- Create: `docs/perf/locust-otel-grafana.md` (facts from this run)
- Modify: `docs/runbooks/load-testing.md`, `docs/perf/README.md`, `AGENTS.md` §8 / §0.2 / §10.5

Do not invent PromQL. After `make stack-up` + `make seed` + `set -a && source .env && set +a`:

1. `make load-locust-otel` (short). Query Prometheus `http://127.0.0.1:9090/api/v1/label/__name__/values` (or `/api/v1/series?match[]={job="otel-collector"}`) and record names that appeared from Locust (typically include locust / http metrics exported via the Collector). Write `locust-otel.json` panels **only** for names you observed. Datasource uid `Prometheus`. Folder via provisioning (same path). Skip partner_slug test via the Task 1 skip/subdir rule.
2. `make load-k6-grafana`. Confirm k6 series exist (e.g. `k6_http_reqs_total` or whatever Prometheus shows — record the **actual** name). Do not claim `k6_http_reqs` if the series is `k6_http_reqs_total`.
3. Rebuild Grafana image if dashboards were added (`make stack-up` already `--build`, or compose build grafana) so baked JSON is in the container.
4. Evidence: commands, metric names list, no invented RPS as NFR. Fail-closed: `LOAD_LOCUST_OTEL=1` with stack **down** (or 4318 closed) must nonzero — if you already have stack up, additionally document network-inspect failure by running the OTEL branch against a missing network in a subshell that cannot see the project network **or** stop collector/4318 only if that would break the rest of the run — preferred: run fail-closed **before** stack-up: `LOAD_LOCUST_OTEL=1 ./scripts/load_smoke.sh` expect FAIL on missing network or missing :4318.
5. `make stack-down` no `-v`. Confirm empty ps.

**Acceptance:** locust-otel.json from live names; k6 19665 provisioned; runbook updated; stack down; no commit.

---

## Out of Wave 2

CI load jobs, expanded pre-commit, `docker-compose.perf.yml`, ceiling hunt, bottleneck fix, k6 breakpoint.
