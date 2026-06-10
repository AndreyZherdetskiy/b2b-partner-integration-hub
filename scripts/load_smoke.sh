#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LOAD_HOST="${LOAD_HOST:-http://127.0.0.1:8000}"
LOAD_USERS="${LOAD_USERS:-2}"
LOAD_SPAWN_RATE="${LOAD_SPAWN_RATE:-1}"
LOAD_RUN_TIME="${LOAD_RUN_TIME:-10s}"
HTML_REPORT=".local/locust/smoke.html"
CSV_PREFIX=".local/locust/smoke"

mkdir -p .local/locust

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
export LOAD_PARTNER_PUBLIC_ID="$partner_public_id"

locust_args=(
  -f loadtests/locustfile.py
  --headless
  --host "$LOAD_HOST"
  -u "$LOAD_USERS"
  -r "$LOAD_SPAWN_RATE"
  -t "$LOAD_RUN_TIME"
  --exit-code-on-error 1
  --html "$HTML_REPORT"
  --csv "$CSV_PREFIX"
)

if [[ "${LOAD_LOCUST_OTEL:-0}" == "1" ]]; then
  if ! docker network inspect b2b-partner-integration-hub >/dev/null 2>&1; then
    echo "LOAD_LOCUST_OTEL=1 requires compose network b2b-partner-integration-hub (make stack-up)" >&2
    exit 1
  fi
  if ! (echo >/dev/tcp/127.0.0.1/4318) 2>/dev/null; then
    echo "LOAD_LOCUST_OTEL=1 requires OTLP HTTP collector on 127.0.0.1:4318" >&2
    exit 1
  fi
  export OTEL_SDK_DISABLED=false
  export OTEL_SERVICE_NAME=locust
  export OTEL_TRACES_EXPORTER=otlp
  export OTEL_METRICS_EXPORTER=otlp
  export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
  export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
  locust_args+=(--otel)
fi

uv run --group load locust "${locust_args[@]}"
