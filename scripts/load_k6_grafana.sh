#!/usr/bin/env bash
# k6 Prometheus remote-write smoke on the compose network (stdin script — WSL-safe).
# Image: grafana/k6:0.54.0 — https://grafana.com/docs/k6/latest/results-output/real-time/prometheus-remote-write/
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_NETWORK=b2b-partner-integration-hub
K6_IMAGE=grafana/k6:0.54.0
K6_VUS="${K6_VUS:-2}"
K6_DURATION="${K6_DURATION:-15s}"

if ! docker network inspect "$COMPOSE_NETWORK" >/dev/null 2>&1; then
  echo "compose network $COMPOSE_NETWORK not found (make stack-up)" >&2
  exit 1
fi

preflight_output="$(uv run python -m loadtests.preflight)"
preflight_status=$?
if [[ $preflight_status -ne 0 ]]; then
  exit "$preflight_status"
fi

partner_public_id="$(echo "$preflight_output" | sed -n 's/.*partner_public_id=\([^[:space:]]*\).*/\1/p')"
if [[ -z "$partner_public_id" ]]; then
  echo "preflight failed: could not parse partner_public_id from stdout" >&2
  exit 1
fi
export K6_PARTNER_PUBLIC_ID="$partner_public_id"

admin_token="${LOAD_ADMIN_TOKEN:-${ADMIN_BOOTSTRAP_TOKEN:-}}"
if [[ -z "$admin_token" ]]; then
  echo "LOAD_ADMIN_TOKEN or ADMIN_BOOTSTRAP_TOKEN is required" >&2
  exit 1
fi

docker run --rm -i \
  --network "$COMPOSE_NETWORK" \
  -e BASE=http://hub-api:8000 \
  -e ADMIN_TOKEN="$admin_token" \
  -e K6_PARTNER_PUBLIC_ID="$K6_PARTNER_PUBLIC_ID" \
  -e K6_VUS="$K6_VUS" \
  -e K6_DURATION="$K6_DURATION" \
  -e K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write \
  "$K6_IMAGE" run -o experimental-prometheus-rw - < load/k6/outbound_ingest.js
