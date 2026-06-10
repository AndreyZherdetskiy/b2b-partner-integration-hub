#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LOAD_HOST="${LOAD_HOST:-http://127.0.0.1:8000}"

if (echo >/dev/tcp/127.0.0.1/8089) 2>/dev/null; then
  echo "FAIL: Locust web UI port 8089 is already in use" >&2
  exit 1
fi

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

uv run --group load locust -f loadtests/locustfile.py --host "$LOAD_HOST"
