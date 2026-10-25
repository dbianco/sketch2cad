#!/usr/bin/env bash
# Compose smoke test: build the images, start db/api/worker/web, wait until every
# service is healthy and check the wiring through HTTP.
#
# Timing: the images are built first (`docker compose build`, reported as
# build_seconds); the 90 s limit applies only to `docker compose up -d --wait`
# with the images already built (reported as elapsed_seconds).
#
# Checks:
#   GET http://127.0.0.1:${WEB_PORT}/               -> 200 (web)
#   GET http://127.0.0.1:${WEB_PORT}/api/v1/auth/me -> 401 problem+json (web -> api -> db)
#   GET http://127.0.0.1:8000/healthz inside api    -> 200 (api -> db)
#
# Secrets are throwaway random values generated in memory and exported only to this
# process and its children; they are never written to disk. The stack, including its
# volumes, is always removed on exit (`down -v`) unless KEEP_UP=1.
#
# Optional environment variables:
#   WEB_PORT          host port of the web service (default 18080)
#   APP_ENV           application environment (default dev)
#   SIGNING_KEY_PATH  signing key path passed to the api (default placeholder; the key
#                     file is not read until H4)
#   KEEP_UP=1         leave the stack running after the checks
#   MAX_UP_SECONDS    limit for elapsed_seconds (default 90)
#
# Usage (from sketch2cad-api/): bash scripts/compose_smoke.sh
set -euo pipefail

api_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$api_dir"

PROJECT="sketch2cad"
MAX_UP_SECONDS="${MAX_UP_SECONDS:-90}"
export WEB_PORT="${WEB_PORT:-18080}"
export APP_ENV="${APP_ENV:-dev}"
export SIGNING_KEY_PATH="${SIGNING_KEY_PATH:-/run/sketch2cad/signing-key.pem}"
POSTGRES_PASSWORD="$(openssl rand -hex 24)"
SESSION_SECRET="$(openssl rand -hex 32)"
export POSTGRES_PASSWORD SESSION_SECRET

compose() {
  docker compose -p "$PROJECT" "$@"
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

if [ -n "$(compose ps -a -q)" ]; then
  fail "compose project '$PROJECT' already has containers; bring it down first"
fi

teardown() {
  local status=$?
  if [ "${KEEP_UP:-0}" = "1" ]; then
    echo "KEEP_UP=1: stack left running. Tear down with:"
    echo "  POSTGRES_PASSWORD=x SESSION_SECRET=x SIGNING_KEY_PATH=x APP_ENV=x docker compose -p $PROJECT down -v"
  else
    echo "Tearing down (down -v)"
    compose down -v --remove-orphans >/dev/null 2>&1 || echo "WARN: teardown failed" >&2
  fi
  exit "$status"
}
trap teardown EXIT

echo "Step 1/3: docker compose build"
build_start=$(date +%s)
compose build
build_seconds=$(( $(date +%s) - build_start ))
echo "build_seconds=${build_seconds}"

echo "Step 2/3: docker compose up -d --wait"
up_start=$(date +%s)
up_status=0
compose up -d --wait --wait-timeout 180 || up_status=$?
elapsed_seconds=$(( $(date +%s) - up_start ))
echo "elapsed_seconds=${elapsed_seconds}"
if [ "$up_status" -ne 0 ]; then
  compose ps -a >&2 || true
  fail "docker compose up --wait exited with status ${up_status}"
fi

echo "Step 3/3: HTTP checks"
web_url="http://127.0.0.1:${WEB_PORT}"

status=$(curl -sS -o /dev/null -w '%{http_code}' "${web_url}/") || fail "GET / unreachable"
[ "$status" = "200" ] || fail "GET / returned ${status}, expected 200"
echo "OK GET / -> ${status}"

result=$(curl -sS -o /dev/null -w '%{http_code} %{content_type}' "${web_url}/api/v1/auth/me") \
  || fail "GET /api/v1/auth/me unreachable"
status="${result%% *}"
content_type="${result#* }"
[ "$status" = "401" ] || fail "GET /api/v1/auth/me returned ${status}, expected 401"
case "$content_type" in
  application/problem+json*) ;;
  *) fail "GET /api/v1/auth/me returned content type '${content_type}', expected problem+json" ;;
esac
echo "OK GET /api/v1/auth/me -> ${status} ${content_type}"

status=$(compose exec -T api python -c "
import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).status)
") || fail "GET /healthz inside api failed"
[ "$status" = "200" ] || fail "GET /healthz returned ${status}, expected 200"
echo "OK GET /healthz (api) -> ${status}"

if [ "$elapsed_seconds" -gt "$MAX_UP_SECONDS" ]; then
  fail "elapsed_seconds=${elapsed_seconds} is above ${MAX_UP_SECONDS}"
fi
echo "PASS: all services healthy in ${elapsed_seconds} s (limit ${MAX_UP_SECONDS} s)"
