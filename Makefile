.PHONY: help ci lint typecheck test-unit test-integration test-e2e test-contract asyncapi-validate \
	compose-up compose-down compose-logs seed seed-prod-like export-openapi migrate load-k6 \
	stack-up stack-down perf-up load-harness load-locust load-locust-ui load-locust-otel load-k6-grafana

# Explicit -p beats COMPOSE_PROJECT_NAME env so sibling stacks never collide.
COMPOSE_PROJECT ?= b2b-partner-integration-hub
COMPOSE ?= docker compose -p $(COMPOSE_PROJECT)

LOAD_HOST ?= http://127.0.0.1:8000
LOAD_USERS ?= 2
LOAD_SPAWN_RATE ?= 1
LOAD_RUN_TIME ?= 10s

help:
	@echo "Quality:"
	@echo "  make ci            lint + typecheck + test-unit + test-contract"
	@echo "  make lint          ruff check + format check"
	@echo "  make typecheck     mypy on app/ partner_mock/ celery_app/"
	@echo "  make test-unit     pytest tests/unit"
	@echo "  make test-e2e      optional live smoke (skipped if compose stack down; not in ci)"
	@echo ""
	@echo "OpenAPI:"
	@echo "  make export-openapi  write docs/openapi/openapi.{json,yaml}"
	@echo "  make asyncapi-validate  validate docs/asyncapi/asyncapi.yaml (requires Node)"
	@echo ""
	@echo "Compose / data (Tasks 1+):"
	@echo "  make compose-up / compose-down / compose-logs"
	@echo "  make stack-up / stack-down  aliases for compose-up / compose-down --remove-orphans"
	@echo "  make perf-up  prod-like overlay (4 workers, scaled consumers; not spec §8.1 proof)"
	@echo "  make migrate / seed / seed-prod-like"
	@echo "  make load-k6       k6 outbound ingest (not part of ci)"
	@echo "  make load-k6-grafana  k6 RW smoke on compose network → Prometheus"
	@echo "  make load-harness  load helper pytest + locust --list (no stack; not in ci)"
	@echo "  make load-locust   headless Locust smoke (preflight + HTML/CSV artifacts)"
	@echo "  make load-locust-otel  Locust smoke with --otel → Collector :4318"
	@echo "  make load-locust-ui  Locust web UI on :8089 (preflight first)"

ci: lint typecheck test-unit test-contract

lint:
	uv run ruff check app partner_mock celery_app tests scripts
	uv run ruff format --check app partner_mock celery_app tests scripts

typecheck:
	uv run mypy app partner_mock celery_app

test-unit:
	uv run pytest tests/unit -v

test-integration:
	uv run pytest tests/integration -v

test-e2e:
	uv run pytest tests/e2e -v

test-contract:
	uv run pytest tests/contract -v

asyncapi-validate:
	npx --yes @asyncapi/cli@3 validate docs/asyncapi/asyncapi.yaml

compose-up:
	$(COMPOSE) up -d --build --wait
	docker image prune -af --filter "label=com.docker.compose.project=$(COMPOSE_PROJECT)"

compose-down:
	$(COMPOSE) down

compose-logs:
	$(COMPOSE) logs -f --tail=100

migrate:
	uv run alembic upgrade head

seed:
	uv run python -m scripts.seed_partners

seed-prod-like:
	uv run python -m scripts.seed_prod_like

export-openapi:
	uv run python scripts/generate_openapi.py

stack-up: compose-up

stack-down:
	$(COMPOSE) -f docker-compose.yml -f docker-compose.perf.yml down --remove-orphans

perf-up:
	$(COMPOSE) -f docker-compose.yml -f docker-compose.perf.yml up -d --build --wait \
		--scale hub-outbound-worker=2 --scale hub-outbox-relay=2

load-harness:
	uv sync --python 3.12 --frozen --group load --group dev
	uv run pytest tests/unit/test_load_helpers.py tests/unit/test_load_scripts.py tests/unit/test_load_grafana_helpers.py tests/unit/test_ci_load_jobs.py -q
	uv run --group load locust -f loadtests/locustfile.py --list

load-locust:
	chmod +x scripts/load_smoke.sh
	LOAD_HOST=$(LOAD_HOST) LOAD_USERS=$(LOAD_USERS) LOAD_SPAWN_RATE=$(LOAD_SPAWN_RATE) \
		LOAD_RUN_TIME=$(LOAD_RUN_TIME) ./scripts/load_smoke.sh

load-locust-ui:
	chmod +x scripts/load_locust_ui.sh
	LOAD_HOST=$(LOAD_HOST) ./scripts/load_locust_ui.sh

load-locust-otel:
	chmod +x scripts/load_smoke.sh
	LOAD_LOCUST_OTEL=1 LOAD_HOST=$(LOAD_HOST) LOAD_USERS=$(LOAD_USERS) LOAD_SPAWN_RATE=$(LOAD_SPAWN_RATE) \
		LOAD_RUN_TIME=$(LOAD_RUN_TIME) ./scripts/load_smoke.sh

load-k6-grafana:
	chmod +x scripts/load_k6_grafana.sh
	./scripts/load_k6_grafana.sh

# k6 POST→202 on /internal/v1/outbound/events; requires K6_PARTNER_PUBLIC_ID.
# Stdin script (same as scripts/load_k6_grafana.sh) — WSL Docker bind-mounts of load/k6 often miss the file.
load-k6:
	@test -n "$(K6_PARTNER_PUBLIC_ID)" || (echo "Set K6_PARTNER_PUBLIC_ID (partner public_id from seed)" && exit 1)
	docker run --rm -i --network host \
	  -e BASE="$(or $(BASE),http://127.0.0.1:8000)" \
	  -e ADMIN_TOKEN="$(or $(ADMIN_TOKEN),$(ADMIN_BOOTSTRAP_TOKEN))" \
	  -e K6_PARTNER_PUBLIC_ID="$(K6_PARTNER_PUBLIC_ID)" \
	  -e K6_VUS="$(K6_VUS)" \
	  -e K6_DURATION="$(K6_DURATION)" \
	  grafana/k6 run - < load/k6/outbound_ingest.js
